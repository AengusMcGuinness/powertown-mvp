from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app import models
from backend.app.schemas import BuildingCreate, BuildingOut, BuildingDossierOut, ObservationCreate, ObservationOut

router = APIRouter()


@router.post("", response_model=BuildingOut)
def create_building(payload: BuildingCreate, db: Session = Depends(get_db)):
    park = db.get(models.IndustrialPark, payload.industrial_park_id)
    if not park:
        raise HTTPException(status_code=404, detail="industrial_park not found")

    building = models.Building(
        industrial_park_id=payload.industrial_park_id,
        name=payload.name,
        address=payload.address,
    )
    db.add(building)
    db.commit()
    db.refresh(building)
    return building


@router.get("/{building_id}", response_model=BuildingDossierOut)
def get_building_dossier(building_id: int, db: Session = Depends(get_db)):
    building = db.get(models.Building, building_id)
    if not building:
        raise HTTPException(status_code=404, detail="building not found")

    # Pull observations for this building (newest first is helpful in review)
    observations = (
        db.query(models.Observation)
        .filter(models.Observation.building_id == building_id)
        .order_by(models.Observation.created_at.desc())
        .all()
    )

    # Pull all media assets attached to those observations
    obs_ids = [o.id for o in observations]
    media_assets = []
    if obs_ids:
        media_assets = (
            db.query(models.MediaAsset)
            .filter(models.MediaAsset.observation_id.in_(obs_ids))
            .order_by(models.MediaAsset.created_at.desc())
            .all()
        )

    return {
        "building": building,
        "observations": observations,
        "media_assets": media_assets,
    }


@router.post("/{building_id}/observations", response_model=ObservationOut)
def add_observation(building_id: int, payload: ObservationCreate, db: Session = Depends(get_db)):
    building = db.get(models.Building, building_id)
    if not building:
        raise HTTPException(status_code=404, detail="building not found")

    obs = models.Observation(
        building_id=building_id,
        observer=payload.observer,
        note_text=payload.note_text,
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    return obs
