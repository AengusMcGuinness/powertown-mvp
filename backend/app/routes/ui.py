from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.db import get_db
from backend.app.services.scoring_cache import get_or_compute_building_score
from backend.app.services.storage import build_upload_path, to_served_url

router = APIRouter()

_ALLOWED_MEDIA_TYPES = {"photo", "audio", "card", "other"}


@router.post("/review/buildings/{building_id}/status")
def update_building_status(
    building_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    b = db.get(models.Building, building_id)
    if not b:
        raise HTTPException(status_code=404, detail="building not found")

    status = (status or "").strip().lower()
    if status not in {"new", "reviewed", "shortlisted"}:
        raise HTTPException(status_code=400, detail="invalid status")

    b.status = status
    db.commit()

    # Return user to dossier
    return RedirectResponse(url=f"/review/buildings/{building_id}", status_code=303)


def _truncate(s: str | None, n: int = 160) -> str:
    if not s:
        return ""
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


def _get_or_create_park_with_flag(db: Session, name: str, location: str | None):
    existing = (
        db.query(models.IndustrialPark)
        .filter(models.IndustrialPark.name == name.strip())
        .first()
    )
    if existing:
        # Fill missing location if provided
        if location and not existing.location:
            existing.location = location.strip()
            db.commit()
            db.refresh(existing)
        return existing, False
    created = _get_or_create_park(db, name, location)
    return created, True


def _get_or_create_building_with_flag(
    db: Session, park_id: int, building_name: str, address: str | None
):
    name = building_name.strip()
    existing = (
        db.query(models.Building)
        .filter(
            models.Building.industrial_park_id == park_id,
            models.Building.name.ilike(name),
        )
        .first()
    )
    if existing:
        return existing, False
    created = _get_or_create_building(db, park_id, building_name, address)
    return created, True


@router.get("/bulk")
def bulk_form(request: Request):
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="backend/app/templates")
    return templates.TemplateResponse(
        "bulk_upload.html", {"request": request, "error": ""}
    )


@router.post("/bulk")
async def bulk_import(
    request: Request,
    zip_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Bulk offline import:
    - Accept a ZIP containing manifest.csv + media files
    - For each row in manifest:
        - get/create park
        - get/create building
        - create observation
        - attach referenced files as MediaAsset records
    """
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="backend/app/templates")

    if (
        not zip_file
        or not zip_file.filename
        or not zip_file.filename.lower().endswith(".zip")
    ):
        return templates.TemplateResponse(
            "bulk_upload.html",
            {"request": request, "error": "Please upload a .zip file."},
            status_code=400,
        )

    data = await zip_file.read()
    await zip_file.close()

    # Safety: size cap (MVP). Adjust as needed.
    MAX_ZIP_BYTES = 50 * 1024 * 1024  # 50MB
    if len(data) > MAX_ZIP_BYTES:
        return templates.TemplateResponse(
            "bulk_upload.html",
            {"request": request, "error": "ZIP too large (max 50MB)."},
            status_code=400,
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return templates.TemplateResponse(
            "bulk_upload.html",
            {"request": request, "error": "Invalid ZIP file."},
            status_code=400,
        )

    # Ensure manifest.csv exists at root
    names = zf.namelist()
    if "manifest.csv" not in names:
        return templates.TemplateResponse(
            "bulk_upload.html",
            {"request": request, "error": "ZIP must contain manifest.csv at the root."},
            status_code=400,
        )

    # Read manifest
    manifest_bytes = zf.read("manifest.csv")
    try:
        manifest_text = manifest_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return templates.TemplateResponse(
            "bulk_upload.html",
            {"request": request, "error": "manifest.csv must be UTF-8 encoded."},
            status_code=400,
        )

    reader = csv.DictReader(io.StringIO(manifest_text))
    required = {"park_name", "building_name"}
    if not reader.fieldnames or not required.issubset(
        set([f.strip() for f in reader.fieldnames])
    ):
        return templates.TemplateResponse(
            "bulk_upload.html",
            {
                "request": request,
                "error": "manifest.csv must include columns: park_name, building_name",
            },
            status_code=400,
        )

    imported_rows = 0
    skipped_rows = 0
    created_observations = 0
    attached_files = 0

    missing_files: list[str] = []
    invalid_rows: list[str] = []
    touched_park_ids: set[int] = set()
    imported_files = 0

    # Allowed media types
    allowed_types = _ALLOWED_MEDIA_TYPES

    for row in reader:
        park_name = (row.get("park_name") or "").strip()
        building_name = (row.get("building_name") or "").strip()
        if not park_name or not building_name:
            skipped_rows += 1
            invalid_rows.append(
                f"missing required fields (park_name/building_name): {row}"
            )
            continue

        park_location = (row.get("park_location") or "").strip()
        building_address = (row.get("building_address") or "").strip()
        observer = (row.get("observer") or "").strip()
        note_text = (row.get("note_text") or "").strip()
        media_type = (row.get("media_type") or "other").strip().lower()
        if media_type not in allowed_types:
            media_type = "other"

        files_field = (row.get("files") or "").strip()
        file_names = []
        if files_field:
            # semicolon-separated filenames
            file_names = [f.strip() for f in files_field.split(";") if f.strip()]

        # Get/create park/building
        park, _ = _get_or_create_park_with_flag(db, park_name, park_location or None)
        building, _ = _get_or_create_building_with_flag(
            db, park.id, building_name, building_address or None
        )

        touched_park_ids.add(park.id)

        # Create observation
        obs = models.Observation(
            building_id=building.id,
            observer=(observer if observer else None),
            note_text=(note_text if note_text else None),
        )
        db.add(obs)
        db.commit()
        db.refresh(obs)
        created_observations += 1

        # Attach files
        for fname in file_names:
            # Security: prevent path traversal
            if ".." in fname or fname.startswith("/") or fname.startswith("\\"):
                continue

            if fname not in names:
                missing_files.append(fname)
                continue

            # Read file bytes
            try:
                blob = zf.read(fname)
            except KeyError:
                continue

            # Persist to uploads using your existing helper
            disk_path = build_upload_path(obs.id, Path(fname).name)
            disk_path.write_bytes(blob)

            served_url = to_served_url(disk_path)
            asset = models.MediaAsset(
                observation_id=obs.id,
                media_type=media_type,
                file_path=served_url,
            )
            db.add(asset)
            attached_files += 1

        db.commit()

        # Warm cached score so review is snappy
        try:
            get_or_compute_building_score(db, building.id)
        except Exception:
            # Don't fail import if scoring fails
            pass

        imported_rows += 1

    # Close zip
    zf.close()

    touched_parks = []
    if touched_park_ids:
        touched_parks = (
            db.query(models.IndustrialPark)
            .filter(models.IndustrialPark.id.in_(list(touched_park_ids)))
            .order_by(models.IndustrialPark.created_at.desc())
            .all()
        )

        return templates.TemplateResponse(
            "import_report.html",
            {
                "request": request,
                "report": {
                    "imported_rows": imported_rows,
                    "skipped_rows": skipped_rows,
                    "created_observations": created_observations,
                    "attached_files": attached_files,
                    "missing_files": missing_files[:200],  # cap to avoid huge pages
                    "invalid_rows": invalid_rows[:200],
                    "touched_parks": touched_parks,
                },
            },
        )


@router.post("/bulk/csv")
async def bulk_import_csv(
    request: Request,
    csv_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    CSV-only import (no media).
    Required columns: park_name, building_name
    Optional: park_location, building_address, observer, note_text
    """
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="backend/app/templates")

    if (
        not csv_file
        or not csv_file.filename
        or not csv_file.filename.lower().endswith(".csv")
    ):
        return templates.TemplateResponse(
            "bulk_upload.html",
            {"request": request, "error": "Please upload a .csv file."},
            status_code=400,
        )

    raw = await csv_file.read()
    await csv_file.close()

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return templates.TemplateResponse(
            "bulk_upload.html",
            {"request": request, "error": "CSV must be UTF-8 encoded."},
            status_code=400,
        )

    reader = csv.DictReader(io.StringIO(text))
    required = {"park_name", "building_name"}
    if not reader.fieldnames or not required.issubset(
        set([f.strip() for f in reader.fieldnames])
    ):
        return templates.TemplateResponse(
            "bulk_upload.html",
            {
                "request": request,
                "error": "CSV must include columns: park_name, building_name",
            },
            status_code=400,
        )

    imported_rows = 0
    skipped_rows = 0
    created_observations = 0
    attached_files = 0  # always 0 for CSV-only
    missing_files: list[str] = []
    invalid_rows: list[str] = []
    touched_park_ids: set[int] = set()

    for row in reader:
        park_name = (row.get("park_name") or "").strip()
        building_name = (row.get("building_name") or "").strip()
        if not park_name or not building_name:
            skipped_rows += 1
            invalid_rows.append(
                f"missing required fields (park_name/building_name): {row}"
            )
            continue

        park_location = (row.get("park_location") or "").strip()
        building_address = (row.get("building_address") or "").strip()
        observer = (row.get("observer") or "").strip()
        note_text = (row.get("note_text") or "").strip()

        park, _ = _get_or_create_park_with_flag(db, park_name, park_location or None)
        building, _ = _get_or_create_building_with_flag(
            db, park.id, building_name, building_address or None
        )
        touched_park_ids.add(park.id)

        obs = models.Observation(
            building_id=building.id,
            observer=(observer if observer else None),
            note_text=(note_text if note_text else None),
        )
        db.add(obs)
        db.commit()
        db.refresh(obs)
        created_observations += 1
        imported_rows += 1

        # Warm cache
        try:
            get_or_compute_building_score(db, building.id)
        except Exception:
            pass

    touched_parks = []
    if touched_park_ids:
        touched_parks = (
            db.query(models.IndustrialPark)
            .filter(models.IndustrialPark.id.in_(list(touched_park_ids)))
            .order_by(models.IndustrialPark.created_at.desc())
            .all()
        )

    return templates.TemplateResponse(
        "import_report.html",
        {
            "request": request,
            "report": {
                "imported_rows": imported_rows,
                "skipped_rows": skipped_rows,
                "created_observations": created_observations,
                "attached_files": attached_files,
                "missing_files": missing_files,
                "invalid_rows": invalid_rows[:200],
                "touched_parks": touched_parks,
            },
        },
    )


@router.get("/review")
def review_home(
    request: Request,
    db: Session = Depends(get_db),
    min_score: int = 0,
    since_hours: str | None = None,
    sort: str = "last_activity",  # or "best_score"
    only_active: bool = False,
):
    since_hours_int = None
    if since_hours:
        try:
            since_hours_int = int(since_hours)
        except ValueError:
            since_hours_int = None
    # Show all parks (most recently created first)
    parks = (
        db.query(models.IndustrialPark)
        .order_by(models.IndustrialPark.created_at.desc())
        .all()
    )

    # Compute quick stats for each park: #buildings and last activity timestamp
    park_cards = []
    for p in parks:
        buildings = (
            db.query(models.Building)
            .filter(models.Building.industrial_park_id == p.id)
            .all()
        )
        building_ids = [b.id for b in buildings]

        last_obs = None

        if building_ids:
            last_obs = (
                db.query(models.Observation)
                .filter(models.Observation.building_id.in_(building_ids))
                .order_by(models.Observation.created_at.desc())
                .first()
            )

        best_score = None
        if building_ids:
            # Compute best building score in this park (MVP: compute per building)
            best = -1
            for b in buildings:
                s = get_or_compute_building_score(db, b.id)
                if s.score > best:
                    best = s.score
            best_score = best if best >= 0 else None

        park_cards.append(
            {
                "park": p,
                "building_count": len(buildings),
                "last_activity": (last_obs.created_at if last_obs else None),
                "best_score": best_score,
            }
        )

    # Apply filters
    cutoff = None
    if since_hours_int is not None:
        cutoff = datetime.utcnow() - timedelta(hours=since_hours_int)

    filtered = []
    for c in park_cards:
        la = c["last_activity"]
        bs = c["best_score"]

        if only_active and la is None:
            continue

        if cutoff is not None:
            # If no activity timestamp, treat as failing the filter
            if la is None or la < cutoff:
                continue

        if min_score and min_score > 0:
            # If no score, treat as failing
            if bs is None or bs < min_score:
                continue

        filtered.append(c)

    park_cards = filtered

    # Sorting
    if sort == "best_score":
        park_cards.sort(
            key=lambda x: (
                x["best_score"] is None,
                -(x["best_score"] or 0),
                x["last_activity"] is None,
                x["last_activity"],
            ),
        )
    else:
        # default: last_activity desc, None last
        park_cards.sort(
            key=lambda x: (x["last_activity"] is None, x["last_activity"]), reverse=True
        )

    # Global recent activity (last 15 observations across all parks)
    recent_activity = []

    recent_obs = (
        db.query(models.Observation)
        .order_by(models.Observation.created_at.desc())
        .limit(15)
        .all()
    )

    if recent_obs:
        # Load buildings and parks for lookup
        building_ids = [o.building_id for o in recent_obs]

        buildings = (
            db.query(models.Building).filter(models.Building.id.in_(building_ids)).all()
        )
        buildings_by_id = {b.id: b for b in buildings}

        park_ids = [b.industrial_park_id for b in buildings]
        parks = (
            db.query(models.IndustrialPark)
            .filter(models.IndustrialPark.id.in_(park_ids))
            .all()
        )
        parks_by_id = {p.id: p for p in parks}

        # Media counts per observation (optional but very useful)
        obs_ids = [o.id for o in recent_obs]
        media_counts = {}
        photo_counts = {}

        media = (
            db.query(models.MediaAsset.observation_id, models.MediaAsset.media_type)
            .filter(models.MediaAsset.observation_id.in_(obs_ids))
            .all()
        )

        for oid, mtype in media:
            media_counts[oid] = media_counts.get(oid, 0) + 1
            if (mtype or "").lower() == "photo":
                photo_counts[oid] = photo_counts.get(oid, 0) + 1

        for o in recent_obs:
            b = buildings_by_id.get(o.building_id)
            p = parks_by_id.get(b.industrial_park_id) if b else None

            recent_activity.append(
                {
                    "observation": o,
                    "building": b,
                    "park": p,
                    "snippet": _truncate(o.note_text, 200),
                    "media_count": media_counts.get(o.id, 0),
                    "photo_count": photo_counts.get(o.id, 0),
                }
            )

    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="backend/app/templates")

    return templates.TemplateResponse(
        "review_home.html",
        {
            "request": request,
            "park_cards": park_cards,
            "recent_activity": recent_activity,
            "filters": {
                "min_score": min_score,
                "since_hours": since_hours_int or "",
                "sort": sort,
                "only_active": only_active,
            },
        },
    )


def _get_or_create_building(
    db: Session,
    park_id: int,
    building_name: str,
    address: str | None,
) -> models.Building:
    name = building_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="building name required")

    # Try to find an existing building in the same park (case-insensitive)
    existing = (
        db.query(models.Building)
        .filter(
            models.Building.industrial_park_id == park_id,
            models.Building.name.ilike(name),
        )
        .first()
    )

    if existing:
        return existing

    # Otherwise create a new one
    b = models.Building(
        industrial_park_id=park_id,
        name=name,
        address=(address.strip() if address else None),
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _get_or_create_park(
    db: Session, name: str, location: str | None
) -> models.IndustrialPark:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="industrial park name required")

    park = (
        db.query(models.IndustrialPark)
        .filter(models.IndustrialPark.name == name)
        .first()
    )
    if park:
        # If user provided location and we didn't have one, fill it in (nice UX)
        if location and not park.location:
            park.location = location.strip()
            db.commit()
            db.refresh(park)
        return park

    park = models.IndustrialPark(
        name=name, location=(location.strip() if location else None)
    )
    db.add(park)
    db.commit()
    db.refresh(park)
    return park


@router.get("/capture")
def capture_form(request: Request, db: Session = Depends(get_db)):
    # List parks for a dropdown (optional but helpful)
    parks = (
        db.query(models.IndustrialPark)
        .order_by(models.IndustrialPark.created_at.desc())
        .all()
    )

    # Import templates from main.py without circular import by creating locally here:
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="backend/app/templates")

    return templates.TemplateResponse(
        "capture.html",
        {
            "request": request,
            "parks": parks,
        },
    )


@router.get("/export/observations.csv")
def export_observations_csv(db: Session = Depends(get_db)):
    """
    Export all observations across all parks/buildings as a single CSV.
    Includes basic joins + media counts.
    """
    rows = (
        db.query(
            models.Observation,
            models.Building,
            models.IndustrialPark,
        )
        .join(models.Building, models.Observation.building_id == models.Building.id)
        .join(
            models.IndustrialPark,
            models.Building.industrial_park_id == models.IndustrialPark.id,
        )
        .order_by(models.Observation.created_at.desc())
        .all()
    )

    obs_ids = [o.id for (o, b, p) in rows]
    media_counts = {}
    photo_counts = {}

    if obs_ids:
        media = (
            db.query(models.MediaAsset.observation_id, models.MediaAsset.media_type)
            .filter(models.MediaAsset.observation_id.in_(obs_ids))
            .all()
        )
        for oid, mtype in media:
            media_counts[oid] = media_counts.get(oid, 0) + 1
            if (mtype or "").lower() == "photo":
                photo_counts[oid] = photo_counts.get(oid, 0) + 1

    buf = io.StringIO()
    w = csv.writer(buf)

    w.writerow(
        [
            "observation_id",
            "created_at",
            "observer",
            "note_text",
            "media_count",
            "photo_count",
            "building_id",
            "building_name",
            "building_address",
            "building_status",
            "park_id",
            "park_name",
            "park_location",
        ]
    )

    for o, b, p in rows:
        w.writerow(
            [
                o.id,
                o.created_at,
                o.observer or "",
                (o.note_text or "").replace("\n", " ").strip(),
                media_counts.get(o.id, 0),
                photo_counts.get(o.id, 0),
                b.id,
                b.name,
                b.address or "",
                getattr(b, "status", "new") or "new",
                p.id,
                p.name,
                p.location or "",
            ]
        )

    csv_bytes = buf.getvalue().encode("utf-8")

    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=observations.csv"},
    )


@router.post("/capture")
async def capture_submit(
    request: Request,
    park_name: str = Form(""),
    park_id: int | None = Form(None),
    park_location: str = Form(""),
    building_name: str = Form(...),
    building_address: str = Form(""),
    observer: str = Form(""),
    note_text: str = Form(""),
    media_type: str = Form("photo"),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    """
    Field-friendly single submission:
    - select existing park OR type a new park name
    - create building
    - create an observation
    - upload 0+ files linked to that observation
    """
    # Choose park:
    # If park_id is provided (selected from dropdown), use it.
    # Otherwise, create/find by park_name.
    park = None
    if park_id:
        park = db.get(models.IndustrialPark, park_id)
        if not park:
            raise HTTPException(
                status_code=404, detail="selected industrial park not found"
            )
    else:
        park = _get_or_create_park(db, park_name, park_location)

    building = _get_or_create_building(db, park.id, building_name, building_address)

    obs = models.Observation(
        building_id=building.id,
        observer=(observer.strip() if observer else None),
        note_text=(note_text.strip() if note_text else None),
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)

    mt = (media_type or "photo").strip().lower()
    if mt not in _ALLOWED_MEDIA_TYPES:
        mt = "other"

    # Save uploaded files (0+)
    for f in files:
        if not f or not f.filename:
            continue
        disk_path = build_upload_path(obs.id, f.filename)
        try:
            contents = await f.read()
            disk_path.write_bytes(contents)
        finally:
            await f.close()

        served_url = to_served_url(disk_path)
        asset = models.MediaAsset(
            observation_id=obs.id,
            media_type=mt,
            file_path=served_url,  # Option A: store served URL
        )
        db.add(asset)

    db.commit()

    # Warm score cache so review pages are instant
    get_or_compute_building_score(db, building.id)

    # Redirect to the building dossier page
    return RedirectResponse(url=f"/review/buildings/{building.id}", status_code=303)


@router.get("/review/parks/{park_id}")
def review_park(
    request: Request, park_id: int, status: str = "", db: Session = Depends(get_db)
):
    park = db.get(models.IndustrialPark, park_id)
    if not park:
        raise HTTPException(status_code=404, detail="industrial park not found")

    buildings = (
        db.query(models.Building)
        .filter(models.Building.industrial_park_id == park_id)
        .all()
    )

    status_filter = (status or "").strip().lower()
    if status_filter in {"new", "reviewed", "shortlisted"}:
        buildings = [b for b in buildings if (b.status or "new") == status_filter]

    building_cards = []
    for b in buildings:
        s = get_or_compute_building_score(db, b.id)
        building_cards.append({"building": b, "score": s})

    # Sort descending by score
    building_cards.sort(key=lambda x: x["score"].score, reverse=True)

    # Park summary stats
    scores = [c["score"].score for c in building_cards]
    park_summary = {
        "building_count": len(building_cards),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "count_70_plus": sum(1 for s in scores if s >= 70),
        "count_50_plus": sum(1 for s in scores if s >= 50),
    }

    top_candidates = building_cards[:3]

    # Recent activity feed (last 15 observations across park), including building name
    building_ids = [c["building"].id for c in building_cards]
    recent_activity = []

    if building_ids:
        recent_obs = (
            db.query(models.Observation)
            .filter(models.Observation.building_id.in_(building_ids))
            .order_by(models.Observation.created_at.desc())
            .limit(15)
            .all()
        )

        # Build a lookup for building_id -> building
        buildings_by_id = {c["building"].id: c["building"] for c in building_cards}

        # Optional: media counts per observation (nice signal)
        obs_ids = [o.id for o in recent_obs]
        media_counts = {}
        photo_counts = {}
        if obs_ids:
            media = (
                db.query(models.MediaAsset.observation_id, models.MediaAsset.media_type)
                .filter(models.MediaAsset.observation_id.in_(obs_ids))
                .all()
            )
            for oid, mtype in media:
                media_counts[oid] = media_counts.get(oid, 0) + 1
                if (mtype or "").lower() == "photo":
                    photo_counts[oid] = photo_counts.get(oid, 0) + 1

        for o in recent_obs:
            b = buildings_by_id.get(o.building_id)
            recent_activity.append(
                {
                    "observation": o,
                    "building": b,
                    "snippet": _truncate(o.note_text, 200),
                    "media_count": media_counts.get(o.id, 0),
                    "photo_count": photo_counts.get(o.id, 0),
                }
            )

    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="backend/app/templates")

    return templates.TemplateResponse(
        "review_park.html",
        {
            "request": request,
            "park": park,
            "building_cards": building_cards,
            "top_candidates": top_candidates,
            "park_summary": park_summary,
            "recent_activity": recent_activity,
            "status_filter": status_filter,
        },
    )


@router.get("/review/buildings/{building_id}")
def review_building(request: Request, building_id: int, db: Session = Depends(get_db)):
    building = db.get(models.Building, building_id)
    if not building:
        raise HTTPException(status_code=404, detail="building not found")

    observations = (
        db.query(models.Observation)
        .filter(models.Observation.building_id == building_id)
        .order_by(models.Observation.created_at.desc())
        .all()
    )

    score = get_or_compute_building_score(db, building_id)

    obs_ids = [o.id for o in observations]
    media_by_obs: dict[int, list[models.MediaAsset]] = {oid: [] for oid in obs_ids}

    if obs_ids:
        media_assets = (
            db.query(models.MediaAsset)
            .filter(models.MediaAsset.observation_id.in_(obs_ids))
            .order_by(models.MediaAsset.created_at.desc())
            .all()
        )
        for m in media_assets:
            media_by_obs.setdefault(m.observation_id, []).append(m)

    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="backend/app/templates")

    return templates.TemplateResponse(
        "review_building.html",
        {
            "request": request,
            "building": building,
            "observations": observations,
            "media_by_obs": media_by_obs,
            "score": score,
        },
    )


@router.get("/search")
def search(request: Request, q: str = "", db: Session = Depends(get_db)):
    term = (q or "").strip()
    parks = []
    buildings = []
    observations = []

    if term:
        like = f"%{term}%"

        parks = (
            db.query(models.IndustrialPark)
            .filter(
                (models.IndustrialPark.name.ilike(like))
                | (models.IndustrialPark.location.ilike(like))
            )
            .order_by(models.IndustrialPark.created_at.desc())
            .limit(25)
            .all()
        )

        buildings = (
            db.query(models.Building)
            .filter(
                (models.Building.name.ilike(like))
                | (models.Building.address.ilike(like))
            )
            .order_by(models.Building.created_at.desc())
            .limit(25)
            .all()
        )

        # Search observation text
        observations = (
            db.query(models.Observation)
            .filter(models.Observation.note_text.ilike(like))
            .order_by(models.Observation.created_at.desc())
            .limit(50)
            .all()
        )

    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="backend/app/templates")

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "q": term,
            "parks": parks,
            "buildings": buildings,
            "observations": observations,
        },
    )
