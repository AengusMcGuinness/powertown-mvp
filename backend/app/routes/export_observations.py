from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.db import get_db

router = APIRouter()


@router.get("/observations.csv")
def export_observations_csv(
    park_id: Optional[int] = Query(default=None),
    building_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Export observations as a flat CSV for analysis, auditing, or map ingestion.
    Filters:
      - park_id: export observations for buildings in a park
      - building_id: export observations for one building
    """
    # Base query
    q = (
        db.query(models.Observation, models.Building, models.IndustrialPark)
        .join(models.Building, models.Observation.building_id == models.Building.id)
        .join(
            models.IndustrialPark,
            models.Building.industrial_park_id == models.IndustrialPark.id,
        )
    )

    if building_id is not None:
        q = q.filter(models.Building.id == building_id)
    if park_id is not None:
        q = q.filter(models.IndustrialPark.id == park_id)

    rows = q.order_by(models.Observation.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "observation_id",
            "observed_at",
            "observer",
            "note_text",
            "building_id",
            "building_name",
            "building_address",
            "park_id",
            "park_name",
            "park_location",
        ]
    )

    for obs, b, p in rows:
        writer.writerow(
            [
                obs.id,
                obs.created_at.isoformat() if obs.created_at else "",
                obs.observer or "",
                (obs.note_text or "").replace("\n", " ").strip(),
                b.id,
                b.name,
                b.address or "",
                p.id,
                p.name,
                p.location or "",
            ]
        )

    csv_bytes = output.getvalue().encode("utf-8")
    filename = "powertown_observations.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
