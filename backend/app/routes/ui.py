from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app import models
from backend.app.services.storage import build_upload_path, to_served_url

router = APIRouter()

_ALLOWED_MEDIA_TYPES = {"photo", "audio", "card", "other"}

@router.get("/review")
def review_home(request: Request, db: Session = Depends(get_db)):
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

        park_cards.append(
            {
                "park": p,
                "building_count": len(buildings),
                "last_activity": (last_obs.created_at if last_obs else None),
            }
        )

    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="backend/app/templates")

    return templates.TemplateResponse(
        "review_home.html",
        {
            "request": request,
            "park_cards": park_cards,
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

def _get_or_create_park(db: Session, name: str, location: str | None) -> models.IndustrialPark:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="industrial park name required")

    park = db.query(models.IndustrialPark).filter(models.IndustrialPark.name == name).first()
    if park:
        # If user provided location and we didn't have one, fill it in (nice UX)
        if location and not park.location:
            park.location = location.strip()
            db.commit()
            db.refresh(park)
        return park

    park = models.IndustrialPark(name=name, location=(location.strip() if location else None))
    db.add(park)
    db.commit()
    db.refresh(park)
    return park


@router.get("/capture")
def capture_form(request: Request, db: Session = Depends(get_db)):
    # List parks for a dropdown (optional but helpful)
    parks = db.query(models.IndustrialPark).order_by(models.IndustrialPark.created_at.desc()).all()

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
            raise HTTPException(status_code=404, detail="selected industrial park not found")
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

    # Redirect to the building dossier page
    return RedirectResponse(url=f"/review/buildings/{building.id}", status_code=303)


@router.get("/review/parks/{park_id}")
def review_park(request: Request, park_id: int, db: Session = Depends(get_db)):
    park = db.get(models.IndustrialPark, park_id)
    if not park:
        raise HTTPException(status_code=404, detail="industrial park not found")

    from backend.app.services.scoring import score_building

    buildings = (
        db.query(models.Building)
        .filter(models.Building.industrial_park_id == park_id)
        .all()
    )
    
    building_cards = []
    for b in buildings:
        obs_texts = (
            db.query(models.Observation.note_text)
            .filter(models.Observation.building_id == b.id)
            .all()
        )
        obs_texts = [t[0] for t in obs_texts]
        s = score_building(obs_texts)
        building_cards.append({"building": b, "score": s})

    # Sort descending by score
    building_cards.sort(key=lambda x: x["score"].score, reverse=True)

    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="backend/app/templates")

    return templates.TemplateResponse(
    "review_park.html",
    {
        "request": request,
        "park": park,
        "building_cards": building_cards,  # ← this name must match
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

    from backend.app.services.scoring import score_building
    score = score_building([o.note_text for o in observations])

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
