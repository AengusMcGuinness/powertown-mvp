from __future__ import annotations

import argparse
import base64
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from backend.app.db import SessionLocal, init_db
from backend.app import models
from backend.app.services.storage import build_upload_path, to_served_url


DEMO_PARK_NAME = "Demo Industrial Park"
DEMO_PARK_LOCATION = "Fall River, MA (demo)"


# 1x1 transparent PNG (very small) so you can render thumbnails without needing real images.
_PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
    "ASsJTYQAAAAASUVORK5CYII="
)


def _png_bytes() -> bytes:
    return base64.b64decode(_PNG_1X1_BASE64)


def _get_or_create_demo_park(db: Session) -> models.IndustrialPark:
    park = db.query(models.IndustrialPark).filter(models.IndustrialPark.name == DEMO_PARK_NAME).first()
    if park:
        # ensure location is set
        if not park.location:
            park.location = DEMO_PARK_LOCATION
            db.commit()
            db.refresh(park)
        return park

    park = models.IndustrialPark(name=DEMO_PARK_NAME, location=DEMO_PARK_LOCATION)
    db.add(park)
    db.commit()
    db.refresh(park)
    return park


def _delete_upload_folder_for_observation(obs_id: int) -> None:
    # storage uses data/uploads/obs_<id>/
    folder = Path("data/uploads") / f"obs_{obs_id}"
    if folder.exists() and folder.is_dir():
        for p in folder.glob("*"):
            try:
                p.unlink()
            except Exception:
                pass
        try:
            folder.rmdir()
        except Exception:
            pass


def _reset_demo_data(db: Session) -> None:
    """
    Deletes existing demo park (and its buildings/observations/media) if present.
    Also cleans up any upload folders for those observations.
    """
    park = db.query(models.IndustrialPark).filter(models.IndustrialPark.name == DEMO_PARK_NAME).first()
    if not park:
        return

    # Collect buildings
    buildings = db.query(models.Building).filter(models.Building.industrial_park_id == park.id).all()
    building_ids = [b.id for b in buildings]

    # Collect observations
    observations = []
    if building_ids:
        observations = db.query(models.Observation).filter(models.Observation.building_id.in_(building_ids)).all()
    obs_ids = [o.id for o in observations]

    # Delete media assets first
    if obs_ids:
        db.query(models.MediaAsset).filter(models.MediaAsset.observation_id.in_(obs_ids)).delete(synchronize_session=False)
        db.commit()

    # Delete observation rows
    if obs_ids:
        db.query(models.Observation).filter(models.Observation.id.in_(obs_ids)).delete(synchronize_session=False)
        db.commit()

    # Delete buildings
    if building_ids:
        db.query(models.Building).filter(models.Building.id.in_(building_ids)).delete(synchronize_session=False)
        db.commit()

    # Delete the park
    db.delete(park)
    db.commit()

    # Clean up upload folders on disk
    for oid in obs_ids:
        _delete_upload_folder_for_observation(oid)


def _create_observation_with_optional_media(
    db: Session,
    building_id: int,
    observer: str,
    note_text: str,
    add_photo: bool = False,
) -> models.Observation:
    obs = models.Observation(building_id=building_id, observer=observer, note_text=note_text)
    db.add(obs)
    db.commit()
    db.refresh(obs)

    if add_photo:
        # Write a tiny png placeholder
        disk_path = build_upload_path(obs.id, "demo_photo.png")
        disk_path.write_bytes(_png_bytes())
        served_url = to_served_url(disk_path)

        asset = models.MediaAsset(
            observation_id=obs.id,
            media_type="photo",
            file_path=served_url,
        )
        db.add(asset)
        db.commit()

    return obs


def seed_demo(db: Session) -> int:
    park = _get_or_create_demo_park(db)

    building_specs = [
        # (name, address, observations[])
        (
            "Matouk Factory",
            "Approx: Textile plant near main road",
            [
                "Large paved lot; visible HVAC units. Mentioned transformer near loading dock. Facilities manager gave business card.",
                "Cold storage area reported; three-phase service likely. Significant truck traffic and distribution activity.",
            ],
        ),
        (
            "Riverside Cold Storage",
            "Rear entrance off service lane",
            [
                "Refrigeration compressors audible; multiple chillers. Switchgear cabinet visible near side wall.",
                "Ample yard space behind building; forklifts and loading docks active. Contact: maintenance supervisor @ example.com.",
            ],
        ),
        (
            "South Bay Logistics",
            "Warehouse row, unit 12",
            [
                "High bay warehouse; heavy forklift activity. Large parking lot with unused corner suitable for containers.",
                "No direct electrical info yet. Need follow-up on utility service size; ask for facilities contact.",
            ],
        ),
        (
            "Fall River Plastics",
            "Corner lot, near substation fence",
            [
                "Manufacturing floor; odor + machinery noise. Substation fence adjacent; transformer signage nearby.",
                "Spoke with receptionist; facilities manager name obtained; follow-up requested.",
            ],
        ),
        (
            "Harbor Metal Works",
            "Unit 7A",
            [
                "Welding/industrial load likely. Switchyard/substation visible across street; three-phase lines overhead.",
                "Tight siting—limited yard. Might need creative placement; ask about leasing adjacent space.",
            ],
        ),
        (
            "Bayview Distribution",
            "Dock-facing frontage",
            [
                "Multiple loading docks and trucks. Large paved staging area; good siting potential.",
                "Solar panels on roof; inverter boxes near utility room. Strong candidate; get electrical single-line diagram.",
            ],
        ),
        (
            "Granite Paper Co.",
            "Main plant, north side",
            [
                "HVAC + chiller plant visible. Transformer pad with warning labels; likely high service capacity.",
                "Contact: facilities@paperco.example. Mentioned interest in demand management.",
            ],
        ),
        (
            "Pier 9 Storage",
            "Small warehouse cluster",
            [
                "Minimal activity; unclear load. Plenty of space but unknown utility service.",
                "No contacts found. Might deprioritize unless utility upgrades are easy.",
            ],
        ),
    ]

    created_buildings = 0

    for name, address, notes in building_specs:
        # Create building (demo data can create new each run only if reset; otherwise reuse by exact match)
        existing = (
            db.query(models.Building)
            .filter(
                models.Building.industrial_park_id == park.id,
                models.Building.name == name,
            )
            .first()
        )
        if existing:
            building = existing
        else:
            building = models.Building(
                industrial_park_id=park.id,
                name=name,
                address=address,
            )
            db.add(building)
            db.commit()
            db.refresh(building)
            created_buildings += 1

        # Create 2 observations each; add a photo to the first for a subset so UI shows thumbnails
        for i, text in enumerate(notes):
            _create_observation_with_optional_media(
                db=db,
                building_id=building.id,
                observer="Demo Seeder",
                note_text=text,
                add_photo=(i == 0),  # attach photo to first obs for each building
            )

    return created_buildings


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data for Powertown MVP.")
    parser.add_argument("--reset", action="store_true", help="Delete existing demo data before seeding.")
    args = parser.parse_args()

    # Ensure tables exist
    init_db()

    db = SessionLocal()
    try:
        if args.reset:
            _reset_demo_data(db)

        created = seed_demo(db)
        print(f"Seeded demo park '{DEMO_PARK_NAME}'. New buildings created: {created}")
        print("Open: http://127.0.0.1:8000/review")
    finally:
        db.close()


if __name__ == "__main__":
    main()
