from __future__ import annotations

import datetime as dt
from datetime import datetime

from sqlalchemy import (Column, DateTime, Float, ForeignKey, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BuildingScoreCache(Base):
    __tablename__ = "building_score_cache"
    __table_args__ = (
        UniqueConstraint(
            "building_id", "version", name="uq_score_cache_building_version"
        ),
    )

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False)

    version = Column(String, nullable=False, default="v1")
    input_hash = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)

    updated_at = Column(DateTime, nullable=False, default=dt.datetime.utcnow)

    building = relationship("Building")


class BuildingScore(Base):
    __tablename__ = "building_scores"
    __table_args__ = (
        UniqueConstraint("building_id", name="uq_building_scores_building_id"),
    )

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False)

    score = Column(Integer, nullable=False)
    confidence = Column(String, nullable=False, default="unknown")
    drivers = Column(
        Text, nullable=False, default=""
    )  # store as JSON string for simplicity

    version = Column(String, nullable=False, default="v1")
    input_hash = Column(String, nullable=False)  # hash of all observation note_text
    updated_at = Column(DateTime, nullable=False, default=dt.datetime.utcnow)

    building = relationship("Building")


class IndustrialPark(Base):
    __tablename__ = "industrial_parks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    buildings: Mapped[list["Building"]] = relationship(back_populates="industrial_park")


class Building(Base):
    __tablename__ = "buildings"

    status = Column(String, nullable=False, default="new")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industrial_park_id: Mapped[int] = mapped_column(
        ForeignKey("industrial_parks.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    industrial_park: Mapped["IndustrialPark"] = relationship(back_populates="buildings")
    observations: Mapped[list["Observation"]] = relationship(back_populates="building")


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"), nullable=False)
    observer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    building: Mapped["Building"] = relationship(back_populates="observations")
    media_assets: Mapped[list["MediaAsset"]] = relationship(
        back_populates="observation"
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id"), nullable=False
    )
    media_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # photo/audio/card/other
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    observation: Mapped["Observation"] = relationship(back_populates="media_assets")
