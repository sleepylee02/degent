"""Read-only database helpers for the replay dashboard API.

dashboard.db  — event_timeline, visualization_states, cluster_snapshots, recommendations
movies.db     — movies (title, poster_url, genres, release_year)  (ATTACHed as 'movies')
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def connect(db_path: str | Path) -> sqlite3.Connection:
    """One-shot connection (used by legacy callers / tests)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def open_persistent(
    dashboard_db: str | Path,
    movies_db: str | Path,
) -> sqlite3.Connection:
    """Open dashboard DB once and ATTACH movies DB.

    Returns a long-lived connection meant to be reused across requests.
    SQLite read-only shared-cache mode keeps the page cache warm between calls.
    """
    conn = sqlite3.connect(
        f"file:{dashboard_db}?mode=ro&cache=shared", uri=True, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA cache_size = -32768")   # 32 MB page cache
    conn.execute("PRAGMA temp_store  = MEMORY")
    if Path(movies_db).exists():
        conn.execute(f"ATTACH DATABASE 'file:{movies_db}?mode=ro' AS movies")
    return conn


# ---------------------------------------------------------------------------
# User discovery
# ---------------------------------------------------------------------------

def list_user_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM event_timeline ORDER BY user_id"
    ).fetchall()
    return [r["user_id"] for r in rows]


# ---------------------------------------------------------------------------
# event_timeline
# ---------------------------------------------------------------------------

def get_timeline(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_id, timestamp, movie_id,
               is_refit_triggered, refit_reason, k_count, noise_count
        FROM event_timeline
        WHERE user_id = ?
        ORDER BY event_id
        """,
        (user_id,),
    ).fetchall()
    return [
        {
            "user_id": user_id,
            "event_id": r["event_id"],
            "timestamp": r["timestamp"],
            "movie_id": r["movie_id"],
            "is_refit_triggered": r["is_refit_triggered"],
            "refit_reason": r["refit_reason"],
            "k_count": r["k_count"],
            "noise_count": r["noise_count"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# visualization_states
# ---------------------------------------------------------------------------

def get_viz_state(conn: sqlite3.Connection, user_id: int, event_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT points_data FROM visualization_states WHERE user_id=? AND event_id=?",
        (user_id, event_id),
    ).fetchone()
    points = json.loads(row["points_data"]) if row else []
    return {"user_id": user_id, "event_id": event_id, "points_data": points}


def get_viz_chunk(
    conn: sqlite3.Connection, user_id: int, checkpoint_id: int
) -> dict[int, dict[str, Any]]:
    """Return all viz rows in one checkpoint segment, keyed by event_id."""
    try:
        rows = conn.execute(
            """SELECT event_id, checkpoint_id, points_data
               FROM visualization_states
               WHERE user_id=? AND checkpoint_id=?
               ORDER BY event_id""",
            (user_id, checkpoint_id),
        ).fetchall()
        return {
            r["event_id"]: {
                "checkpoint_id": r["checkpoint_id"],
                "points_data": json.loads(r["points_data"]) if r["points_data"] else [],
            }
            for r in rows
        }
    except sqlite3.OperationalError:
        row = conn.execute(
            "SELECT event_id, points_data FROM visualization_states WHERE user_id=? AND event_id=?",
            (user_id, checkpoint_id),
        ).fetchone()
        if not row:
            return {}
        return {
            row["event_id"]: {
                "checkpoint_id": checkpoint_id,
                "points_data": json.loads(row["points_data"]) if row["points_data"] else [],
            }
        }


# ---------------------------------------------------------------------------
# cluster_snapshots
# ---------------------------------------------------------------------------

def get_cluster_info(
    conn: sqlite3.Connection, user_id: int, event_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT cluster_id, size, top_genres
        FROM cluster_snapshots
        WHERE user_id=? AND event_id=?
        ORDER BY cluster_id
        """,
        (user_id, event_id),
    ).fetchall()
    return [
        {
            "user_id": user_id,
            "event_id": event_id,
            "cluster_id": r["cluster_id"],
            "size": r["size"],
            "top_genres": json.loads(r["top_genres"]) if r["top_genres"] else [],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# recommendations — movies joined via ATTACH (single connection)
# ---------------------------------------------------------------------------

def get_recommendations(
    conn: sqlite3.Connection,
    user_id: int,
    event_id: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.rank, r.movie_id, r.score, r.src_cluster,
               m.title, m.poster_url, m.genres, m.release_year
        FROM recommendations r
        LEFT JOIN movies.movies m USING (movie_id)
        WHERE r.user_id=? AND r.event_id=?
        ORDER BY r.rank
        """,
        (user_id, event_id),
    ).fetchall()
    return [
        {
            "user_id": user_id,
            "event_id": event_id,
            "rank": r["rank"],
            "movie_id": r["movie_id"],
            "score": r["score"],
            "src_cluster": r["src_cluster"],
            "title": r["title"],
            "poster_url": r["poster_url"],
            "genres": json.loads(r["genres"]) if r["genres"] else [],
            "release_year": r["release_year"],
        }
        for r in rows
    ]
