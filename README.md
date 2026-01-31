# Powertown Prospecting MVP

A lightweight internal tool for collecting, organizing, and reviewing field observations about commercial and industrial buildings, with the goal of identifying strong battery storage candidates.

This project is designed as an MVP to support Powertown’s on-the-ground prospecting workflow: turning messy, multimodal field notes into structured, decision-ready building dossiers.

---

## Problem Context

Powertown teams collect real-world data in industrial parks: notes, photos, conversations, business cards, and informal observations. This information is inherently messy, subjective, and gathered asynchronously by different people.

The challenge is not data collection alone — it’s **organizing and synthesizing that information** so the team can:
- review what was learned in the field,
- compare buildings within an industrial park,
- and prioritize which sites are strong candidates for battery deployment.

This MVP focuses on that core workflow.

---

## Design Philosophy

- **Append-only observations**  
  Field data should never be overwritten. Observations are timestamped, attributed, and additive.

- **Multimodal by default**  
  Notes, photos, and other media are first-class inputs, not afterthoughts.

- **Transparent heuristics over black boxes**  
  Battery readiness is computed using a simple, explainable scoring function rather than ML.

- **Optimize for speed and clarity**  
  This is a small internal tool meant to be easy to run, easy to inspect, and easy to extend.

---

## Features

- REST API for:
  - Creating buildings
  - Adding observations
  - Uploading media linked to observations
- Relational data model (SQLite)
- Local file storage for media
- Aggregated “building dossier” views
- Simple battery readiness scoring
- Seed data to demonstrate a realistic industrial park workflow

---

## Tech Stack

- **Backend:** FastAPI (Python)
- **Database:** SQLite
- **ORM:** SQLAlchemy
- **File storage:** Local filesystem
- **API docs:** Auto-generated OpenAPI (Swagger UI)

---

## Quickstart

### 1. Clone the repo
```bash
git clone git@github.com:AengusMcGuinness/powertown-mvp.git
cd powertown-mvp
```
### 2. Create and activate a virtual environment
```
python -m venv .venv
source .venv/bin/activate
```
### 3. Install dependencies
```
pip install -r requirements.txt
```
### 4. Initialize the database (one-time)
```
python -m backend.scripts.init_db
```
This will create a local SQLite database at: `backend/app/app.db`
### 5. Run the server
```
uvicorn backend.app.main:app --reload
```

## Development Notes

- Database tables are created explicitly via `backend/scripts/init_db.py`.
- Server startup is intentionally side-effect free.
- This mirrors production workflows where schema changes are controlled and explicit.
- For MVP speed, SQLite is used; the data model is compatible with Postgres


