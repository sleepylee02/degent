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
DASHBOARD_DB = Path(os.environ.get("DASHBOARD_DB_PATH", str(_REPO / "outputs" / "dashboard_optimized.sqlite")))
MOVIES_DB    = Path(os.environ.get("MOVIES_DB_PATH",    str(_REPO / "data" / "movies.db")))
POSTER_DIR   = Path(os.environ.get("POSTER_DIR",        str(_REPO / "data" / "MLP-20M")))

if POSTER_DIR.exists():
    app.mount("/posters", StaticFiles(directory=POSTER_DIR), name="posters")

# ---------------------------------------------------------------------------
# Persistent connection — opened once at startup, reused across all requests.
# movies.db is ATTACHed so every query uses a single connection with no
# per-request file-open overhead.
# ---------------------------------------------------------------------------
_conn: "database.sqlite3.Connection | None" = None

def _get_conn() -> "database.sqlite3.Connection":
    global _conn
    if _conn is None:
        if not DASHBOARD_DB.exists():
            raise HTTPException(status_code=503, detail=f"DB not found: {DASHBOARD_DB}")
        _conn = database.open_persistent(DASHBOARD_DB, MOVIES_DB)
    return _conn


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@app.get("/api/users")
def list_users() -> dict[str, Any]:
    return {"user_ids": database.list_user_ids(_get_conn())}


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@app.get("/api/users/{user_id}/timeline")
def get_timeline(user_id: int) -> dict[str, Any]:
    rows = database.get_timeline(_get_conn(), user_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No events found for user")
    return {"user_id": user_id, "timeline": rows}


# ---------------------------------------------------------------------------
# Full dashboard frame  (viz 제외 — 프론트에서 chunk로 처리)
# ---------------------------------------------------------------------------

@app.get("/api/users/{user_id}/events/{event_id}")
def get_dashboard_frame(user_id: int, event_id: int) -> dict[str, Any]:
    conn = _get_conn()
    clusters = database.get_cluster_info(conn, user_id, event_id)
    recs     = database.get_recommendations(conn, user_id, event_id)
    return {
        "user_id": user_id,
        "event_id": event_id,
        "clusters": clusters,
        "recommendations": recs,
    }


# ---------------------------------------------------------------------------
# Sub-endpoints
# ---------------------------------------------------------------------------

@app.get("/api/users/{user_id}/visualization/{checkpoint_id}")
def get_viz_chunk(user_id: int, checkpoint_id: int) -> dict[str, Any]:
    data = database.get_viz_chunk(_get_conn(), user_id, checkpoint_id)
    return {"user_id": user_id, "checkpoint_id": checkpoint_id, "chunk": data}


@app.get("/api/users/{user_id}/events/{event_id}/clusters")
def get_clusters(user_id: int, event_id: int) -> dict[str, Any]:
    rows = database.get_cluster_info(_get_conn(), user_id, event_id)
    return {"user_id": user_id, "event_id": event_id, "clusters": rows}


@app.get("/api/users/{user_id}/events/{event_id}/recommendations")
def get_recommendations(user_id: int, event_id: int) -> dict[str, Any]:
    rows = database.get_recommendations(_get_conn(), user_id, event_id)
    return {"user_id": user_id, "event_id": event_id, "recommendations": rows}
