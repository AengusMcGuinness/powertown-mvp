from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# If you implement DB table creation on startup, you'll import it here later:
# from backend.app.db import init_db

app = FastAPI(
    title="Powertown MVP",
    version="0.1.0",
    description="Minimal internal platform to capture and review multimodal building observations.",
)

# Templates
templates = Jinja2Templates(directory="backend/app/templates")

# Serve uploaded files so reviewers can click links in responses.
uploads_dir = Path("data/uploads")
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

# Optional: serve your own static assets (CSS, etc.)
static_dir = Path("backend/app/static")
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# CORS is useful if you later add a simple web UI running on another port
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For MVP only; lock down later if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Error silencing
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


# --- Route wiring (add these files as you implement them) ---
from backend.app.routes import (buildings, export,  # , media
                                export_observations, observations, parks, ui)

app.include_router(parks.router, prefix="/industrial-parks", tags=["industrial-parks"])
app.include_router(buildings.router, prefix="/buildings", tags=["buildings"])
app.include_router(observations.router, prefix="/observations", tags=["observations"])
app.include_router(ui.router, tags=["ui"])
app.include_router(export.router, prefix="/export", tags=["export"])
app.include_router(export_observations.router, prefix="/export", tags=["export"])
# app.include_router(media.router, prefix="/media", tags=["media"])
