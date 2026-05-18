from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import database

app = FastAPI(title="Replay Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_REPO = Path(__file__).resolve().parent.parent
DASHBOARD_DB = Path(os.environ.get("DASHBOARD_DB_PATH", str(_REPO / "outputs" / "post" / "temporal_2022_events_3000_new_versions_history" / "dashboard_compact" / "dashboard_compact.sqlite")))
MOVIES_DB    = Path(os.environ.get("MOVIES_DB_PATH",    str(_REPO / "data" / "movies.db")))
POSTER_DIR = Path(os.environ.get("POSTER_DIR", str(Path(__file__).resolve().parent.parent / "data" / "MLP-20M")))

if POSTER_DIR.exists():
    app.mount("/posters", StaticFiles(directory=POSTER_DIR), name="posters")


def _dash():
    if not DASHBOARD_DB.exists():
        raise HTTPException(status_code=503, detail=f"DB not found: {DASHBOARD_DB}")
    return database.connect(DASHBOARD_DB)

def _movies():
    if not MOVIES_DB.exists():
        raise HTTPException(status_code=503, detail=f"DB not found: {MOVIES_DB}")
    return database.connect(MOVIES_DB)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@app.get("/api/users")
def list_users() -> dict[str, Any]:
    with _dash() as conn:
        return {"user_ids": database.list_user_ids(conn)}


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@app.get("/api/users/{user_id}/timeline")
def get_timeline(user_id: int) -> dict[str, Any]:
    with _dash() as conn:
        rows = database.get_timeline(conn, user_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No events found for user")
    return {"user_id": user_id, "timeline": rows}


# ---------------------------------------------------------------------------
# Full dashboard frame
# ---------------------------------------------------------------------------

@app.get("/api/users/{user_id}/events/{event_id}")
def get_dashboard_frame(user_id: int, event_id: int) -> dict[str, Any]:
    with _dash() as dash, _movies() as mov:
        viz      = database.get_viz_state(dash, user_id, event_id)
        clusters = database.get_cluster_info(dash, user_id, event_id)
        recs     = database.get_recommendations(dash, mov, user_id, event_id)
    return {
        "user_id": user_id,
        "event_id": event_id,
        "visualization": viz,
        "clusters": clusters,
        "recommendations": recs,
    }


# ---------------------------------------------------------------------------
# Sub-endpoints
# ---------------------------------------------------------------------------

@app.get("/api/users/{user_id}/events/{event_id}/visualization")
def get_visualization(user_id: int, event_id: int) -> dict[str, Any]:
    with _dash() as conn:
        return database.get_viz_state(conn, user_id, event_id)


@app.get("/api/users/{user_id}/events/{event_id}/clusters")
def get_clusters(user_id: int, event_id: int) -> dict[str, Any]:
    with _dash() as conn:
        rows = database.get_cluster_info(conn, user_id, event_id)
    return {"user_id": user_id, "event_id": event_id, "clusters": rows}


@app.get("/api/users/{user_id}/events/{event_id}/recommendations")
def get_recommendations(user_id: int, event_id: int) -> dict[str, Any]:
    with _dash() as dash, _movies() as mov:
        rows = database.get_recommendations(dash, mov, user_id, event_id)
    return {"user_id": user_id, "event_id": event_id, "recommendations": rows}
