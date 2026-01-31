from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.db import get_db
from backend.app.services.scoring import score_building

router = APIRouter()


def _dt_iso(dt: Optional[datetime]) -> str:
    return dt.isoformat() if dt else ""


@router.get("/csv")
def export_csv(
    park_id: Optional[int] = Query(
        default=None, description="Filter by industrial park id"
    ),
    db: Session = Depends(get_db),
):
    """
    Export a CSV suitable for map/spreadsheet workflows.

    Columns include:
      - park info
      - building info
      - readiness score + confidence + drivers
      - observation/media counts + last observed time
    """
    parks_by_id = {p.id: p for p in db.query(models.IndustrialPark).all()}

    # Load buildings (optionally filtered by park)
    q = db.query(models.Building)
    if park_id is not None:
        q = q.filter(models.Building.industrial_park_id == park_id)
    buildings = q.order_by(models.Building.industrial_park_id, models.Building.id).all()

    # Preload observations + media in a way that's simple (MVP) and correct.
    # (Could be optimized further, but this is fine for MVP scale.)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "park_id",
            "park_name",
            "park_location",
            "building_id",
            "building_name",
            "building_address",
            "readiness_score",
            "confidence",
            "top_drivers",
            "observation_count",
            "media_count",
            "photo_count",
            "last_observed_at",
            "building_created_at",
        ]
    )

    for b in buildings:
        park = parks_by_id.get(b.industrial_park_id)

        observations = (
            db.query(models.Observation)
            .filter(models.Observation.building_id == b.id)
            .order_by(models.Observation.created_at.desc())
            .all()
        )
        obs_texts = [o.note_text for o in observations]
        score = score_building(obs_texts)

        obs_ids = [o.id for o in observations]
        media_assets = []
        if obs_ids:
            media_assets = (
                db.query(models.MediaAsset)
                .filter(models.MediaAsset.observation_id.in_(obs_ids))
                .all()
            )

        media_count = len(media_assets)
        photo_count = sum(
            1 for m in media_assets if (m.media_type or "").lower() == "photo"
        )

        last_observed_at = observations[0].created_at if observations else None

        writer.writerow(
            [
                b.industrial_park_id,
                park.name if park else "",
                park.location if park else "",
                b.id,
                b.name,
                b.address or "",
                score.score,
                score.confidence,
                "; ".join(score.drivers),
                len(observations),
                media_count,
                photo_count,
                _dt_iso(last_observed_at),
                _dt_iso(getattr(b, "created_at", None)),
            ]
        )

    filename = (
        "powertown_export.csv"
        if park_id is None
        else f"powertown_export_park_{park_id}.csv"
    )
    csv_bytes = output.getvalue().encode("utf-8")

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
