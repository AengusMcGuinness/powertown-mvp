from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


# ---------- Industrial Parks ----------

class IndustrialParkCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    location: Optional[str] = Field(None, max_length=200)


class IndustrialParkOut(BaseModel):
    id: int
    name: str
    location: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Buildings ----------

class BuildingCreate(BaseModel):
    industrial_park_id: int
    name: str = Field(..., min_length=1, max_length=200)
    address: Optional[str] = Field(None, max_length=300)


class BuildingOut(BaseModel):
    id: int
    industrial_park_id: int
    name: str
    address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Observations ----------

class ObservationCreate(BaseModel):
    observer: Optional[str] = Field(None, max_length=120)
    note_text: Optional[str] = None


class ObservationOut(BaseModel):
    id: int
    building_id: int
    observer: Optional[str]
    note_text: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Media (placeholder for now; returned in dossier) ----------

class MediaAssetOut(BaseModel):
    id: int
    observation_id: int
    media_type: str
    file_path: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Dossier ----------

class BuildingDossierOut(BaseModel):
    building: BuildingOut
    observations: List[ObservationOut]
    media_assets: List[MediaAssetOut]
