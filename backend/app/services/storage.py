from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4


def _safe_filename(name: str) -> str:
    # Remove path separators and make it reasonably filesystem-safe
    name = os.path.basename(name)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "upload"


def uploads_root() -> Path:
    """
    Root directory for stored uploads.
    We keep it outside backend/app/ so it's clearly generated data.
    """
    return Path("data/uploads")


def build_upload_path(observation_id: int, original_filename: str) -> Path:
    """
    Returns a unique path like:
    data/uploads/obs_12/9f3a...__photo.jpg
    """
    safe = _safe_filename(original_filename)
    unique = uuid4().hex
    folder = uploads_root() / f"obs_{observation_id}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{unique}__{safe}"
