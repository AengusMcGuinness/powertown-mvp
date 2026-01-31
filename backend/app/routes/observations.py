from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.db import get_db
from backend.app.schemas import MediaAssetOut
from backend.app.services.storage import build_upload_path

router = APIRouter()

_ALLOWED_MEDIA_TYPES = {"photo", "audio", "card", "other"}


@router.post("/{observation_id}/media", response_model=MediaAssetOut)
async def upload_media(
    observation_id: int,
    media_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload media (photo/audio/etc.) linked to an existing observation.
    Stores file on local filesystem and records metadata in media_assets table.
    """
    obs = db.get(models.Observation, observation_id)
    if not obs:
        raise HTTPException(status_code=404, detail="observation not found")

    media_type_norm = media_type.strip().lower()
    if media_type_norm not in _ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid media_type (allowed: {sorted(_ALLOWED_MEDIA_TYPES)})",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")

    # Save the file to disk
    path = build_upload_path(observation_id, file.filename)

    try:
        contents = await file.read()
        path.write_bytes(contents)
    finally:
        await file.close()

    # Store relative path so the repo can move without breaking
    rel_path = str(path)

    asset = models.MediaAsset(
        observation_id=observation_id,
        media_type=media_type_norm,
        file_path=rel_path,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    return asset
