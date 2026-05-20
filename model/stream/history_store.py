from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

import model.stream.runtime_store as runtime_store
from model.common.runtime import local_timestamp


HISTORY_SCHEMA_VERSION = 2


def _json(value: Any) -> str:
    return runtime_store._json(value)


def _bool(value: Any) -> int:
    return runtime_store._bool(value)


def _none_or_float(value: Any) -> float | None:
    return runtime_store._none_or_float(value)


def _none_or_int(value: Any) -> int | None:
    return runtime_store._none_or_int(value)


def connect(db_path: Path) -> sqlite3.Connection:
    return runtime_store.connect(db_path)


def init_store(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS history_runs (
                run_id TEXT PRIMARY KEY,
                production_db_path TEXT,
                replay_root TEXT,
                history_mode TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS history_state_versions (
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                current_version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS history_run_events (
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                replay_order INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                rating REAL NOT NULL,
                rated_at TEXT NOT NULL,
                rated_at_ts REAL NOT NULL,
                source TEXT,
                payload_json TEXT,
                PRIMARY KEY (run_id, event_id)
            );

            CREATE TABLE IF NOT EXISTS history_stage_events (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                replay_order INTEGER,
                user_id INTEGER,
                source_stage TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_no INTEGER NOT NULL DEFAULT 1,
                started_at TEXT,
                ended_at TEXT,
                latency_sec REAL,
                error_type TEXT,
                error_message TEXT,
                payload_json TEXT,
                recorded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_state_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                state_version TEXT NOT NULL,
                raw_event_count_before INTEGER,
                raw_event_count_after INTEGER,
                positive_event_count_before INTEGER,
                positive_event_count_after INTEGER,
                active_event_count_before INTEGER,
                active_event_count_after INTEGER,
                last_raw_event_id INTEGER,
                positive_projection_json TEXT,
                changed_raw_event_ids_json TEXT,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS embedding_change_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                raw_event_id INTEGER NOT NULL,
                event_idx INTEGER,
                movie_id INTEGER,
                change_type TEXT NOT NULL,
                history_len INTEGER,
                context_start_idx INTEGER,
                signature_hash TEXT,
                active_before_count INTEGER,
                active_after_count INTEGER,
                state_version TEXT,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS assignment_decision_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                raw_event_id INTEGER NOT NULL,
                event_idx INTEGER,
                movie_id INTEGER,
                status TEXT NOT NULL,
                interest_id INTEGER,
                similarity REAL,
                reason TEXT,
                state_version TEXT,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS assignment_projection_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                raw_event_id INTEGER NOT NULL,
                event_idx INTEGER,
                movie_id INTEGER,
                assignment_status TEXT NOT NULL,
                visual_status TEXT NOT NULL,
                interest_id INTEGER,
                candidate_interest_id INTEGER,
                similarity REAL,
                reason TEXT,
                umap_x REAL,
                umap_y REAL,
                base_state_version TEXT,
                base_refit_event_id INTEGER,
                base_refit_replay_order INTEGER,
                projection_source TEXT,
                state_version TEXT,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS user_interest_timeline (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                source_stage TEXT NOT NULL,
                state_version TEXT NOT NULL,
                pending_count INTEGER NOT NULL,
                processed_count INTEGER NOT NULL,
                interest_count INTEGER NOT NULL,
                assigned_since_last_refit INTEGER NOT NULL,
                outlier_since_last_refit INTEGER NOT NULL,
                refit_required INTEGER NOT NULL,
                refit_request_open INTEGER NOT NULL,
                assignment_status TEXT,
                interest_id INTEGER,
                similarity REAL,
                refit_request_id TEXT,
                refit_reasons_json TEXT,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS refit_lifecycle_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                request_id TEXT,
                status TEXT NOT NULL,
                state_version TEXT,
                reasons_json TEXT,
                backend TEXT,
                active_embedding_rows INTEGER,
                interest_count INTEGER,
                elapsed_sec REAL,
                error_type TEXT,
                error_message TEXT,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS interest_vector_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                request_id TEXT,
                state_version TEXT NOT NULL,
                interest_id INTEGER NOT NULL,
                dim INTEGER NOT NULL,
                dtype TEXT NOT NULL,
                vector_blob BLOB NOT NULL,
                assigned_count INTEGER,
                source TEXT,
                top_genres_json TEXT,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS interest_membership_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                request_id TEXT,
                state_version TEXT NOT NULL,
                raw_event_id INTEGER NOT NULL,
                event_idx INTEGER,
                movie_id INTEGER,
                interest_id INTEGER,
                cluster_label INTEGER NOT NULL,
                umap_x REAL,
                umap_y REAL,
                membership_source TEXT,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS recommendation_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_run_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                event_id INTEGER,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                trigger_event_id INTEGER,
                trigger_replay_order INTEGER,
                trigger_state_version TEXT,
                trigger_source_stage TEXT,
                top_k INTEGER NOT NULL,
                normalize INTEGER NOT NULL,
                include_seen INTEGER NOT NULL,
                row_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS recommendation_row_history (
                recommendation_run_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                event_id INTEGER,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                score REAL,
                src_cluster INTEGER,
                item_idx INTEGER,
                metadata_json TEXT,
                PRIMARY KEY (recommendation_run_id, user_id, rank)
            );

            CREATE INDEX IF NOT EXISTS idx_history_run_events_user_order
                ON history_run_events(run_id, user_id, replay_order);
            CREATE INDEX IF NOT EXISTS idx_user_interest_timeline_user_order
                ON user_interest_timeline(run_id, user_id, replay_order, history_id);
            CREATE INDEX IF NOT EXISTS idx_refit_lifecycle_user_order
                ON refit_lifecycle_history(run_id, user_id, replay_order, history_id);
            CREATE INDEX IF NOT EXISTS idx_assignment_projection_user_order
                ON assignment_projection_history(run_id, user_id, replay_order, history_id);
            CREATE INDEX IF NOT EXISTS idx_interest_membership_state
                ON interest_membership_history(run_id, user_id, state_version);
            CREATE INDEX IF NOT EXISTS idx_interest_vector_state
                ON interest_vector_history(run_id, user_id, state_version);
            CREATE INDEX IF NOT EXISTS idx_recommendation_history_user_order
                ON recommendation_history(run_id, user_id, trigger_replay_order, history_id);
            """
        )
        now = local_timestamp()
        conn.execute(
            """
            INSERT INTO schema_meta(key, value, updated_at)
            VALUES ('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (str(HISTORY_SCHEMA_VERSION), now),
        )


def checkpoint(db_path: Path | None) -> None:
    if db_path is None:
        return
    db_path = Path(db_path)
    if not db_path.exists():
        return
    init_store(db_path)
    with connect(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _next_state_version_conn(conn: sqlite3.Connection, *, run_id: str, user_id: int) -> str:
    now = local_timestamp()
    row = conn.execute(
        "SELECT current_version FROM history_state_versions WHERE run_id=? AND user_id=?",
        (run_id, int(user_id)),
    ).fetchone()
    next_version = 1 if row is None else int(row["current_version"]) + 1
    conn.execute(
        """
        INSERT INTO history_state_versions(run_id, user_id, current_version, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(run_id, user_id) DO UPDATE SET
            current_version=excluded.current_version,
            updated_at=excluded.updated_at
        """,
        (run_id, int(user_id), next_version, now),
    )
    return str(next_version)


def get_latest_state_version(db_path: Path, *, run_id: str, user_id: int) -> str | None:
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    init_store(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT current_version FROM history_state_versions WHERE run_id=? AND user_id=?",
            (run_id, int(user_id)),
        ).fetchone()
    return None if row is None else str(int(row["current_version"]))


def record_history_run(
    db_path: Path,
    *,
    run_id: str,
    status: str,
    production_db_path: str | None = None,
    replay_root: str | None = None,
    history_mode: str = "history",
    started_at: str | None = None,
    ended_at: str | None = None,
) -> None:
    init_store(db_path)
    now = local_timestamp()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO history_runs(
                run_id, production_db_path, replay_root, history_mode, schema_version,
                status, started_at, ended_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                production_db_path=COALESCE(excluded.production_db_path, history_runs.production_db_path),
                replay_root=COALESCE(excluded.replay_root, history_runs.replay_root),
                history_mode=excluded.history_mode,
                schema_version=excluded.schema_version,
                status=excluded.status,
                started_at=COALESCE(excluded.started_at, history_runs.started_at),
                ended_at=COALESCE(excluded.ended_at, history_runs.ended_at),
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                production_db_path,
                replay_root,
                history_mode,
                HISTORY_SCHEMA_VERSION,
                status,
                started_at,
                ended_at,
                now,
                now,
            ),
        )


def record_run_event(db_path: Path, *, run_id: str, event: dict[str, Any]) -> None:
    init_store(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO history_run_events(
                run_id, event_id, replay_order, user_id, movie_id, rating, rated_at, rated_at_ts, source, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, event_id) DO UPDATE SET
                replay_order=excluded.replay_order,
                user_id=excluded.user_id,
                movie_id=excluded.movie_id,
                rating=excluded.rating,
                rated_at=excluded.rated_at,
                rated_at_ts=excluded.rated_at_ts,
                source=excluded.source,
                payload_json=excluded.payload_json
            """,
            (
                run_id,
                int(event["eventId"]),
                int(event.get("replayOrder", 0)),
                int(event["userId"]),
                int(event["movieId"]),
                float(event["rating"]),
                str(event["ratedAt"]),
                float(event["ratedAtTs"]),
                event.get("source"),
                _json(event),
            ),
        )


def record_stage_event(
    db_path: Path,
    *,
    run_id: str,
    event_id: int,
    replay_order: int | None,
    source_stage: str,
    status: str,
    user_id: int | None = None,
    attempt_no: int = 1,
    started_at: str | None = None,
    ended_at: str | None = None,
    latency_sec: float | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    init_store(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO history_stage_events(
                run_id, event_id, replay_order, user_id, source_stage, status, attempt_no,
                started_at, ended_at, latency_sec, error_type, error_message, payload_json, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                int(event_id),
                _none_or_int(replay_order),
                _none_or_int(user_id),
                source_stage,
                status,
                int(attempt_no),
                started_at,
                ended_at,
                _none_or_float(latency_sec),
                error_type,
                error_message,
                None if payload is None else _json(payload),
                local_timestamp(),
            ),
        )


def _state_counts_from_payload(payload: dict[str, Any]) -> dict[str, int | None]:
    raw_events = payload.get("rawEvents", [])
    positive_events = payload.get("positiveEvents", [])
    active_events = [event for event in positive_events if event.get("status") == "active"]
    last_raw_event_id = max((int(event["rawEventId"]) for event in raw_events), default=None)
    return {
        "raw": int(payload.get("stats", {}).get("rawEventCount", len(raw_events))),
        "positive": int(payload.get("stats", {}).get("positiveEventCount", len(positive_events))),
        "active": int(payload.get("stats", {}).get("activeEventCount", len(active_events))),
        "last_raw_event_id": last_raw_event_id,
    }


def record_user_state(
    db_path: Path,
    *,
    run_id: str,
    event_id: int,
    replay_order: int | None,
    state: Any,
    changed_raw_event_ids: list[int] | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    init_store(db_path)
    state_payload = state.to_dict()
    user_id = int(state_payload["userId"])
    counts = _state_counts_from_payload(state_payload)
    changed_ids = sorted({int(value) for value in (changed_raw_event_ids or [])})
    positive_projection = [
        {
            "rawEventId": int(item["rawEventId"]),
            "eventIdx": int(item["eventIdx"]),
            "movieId": int(item["movieId"]),
            "status": str(item["status"]),
        }
        for item in state_payload.get("positiveEvents", [])
    ]
    with connect(db_path) as conn:
        previous = conn.execute(
            """
            SELECT raw_event_count_after, positive_event_count_after, active_event_count_after
            FROM user_state_history
            WHERE run_id=? AND user_id=?
            ORDER BY history_id DESC
            LIMIT 1
            """,
            (run_id, user_id),
        ).fetchone()
        state_version = _next_state_version_conn(conn, run_id=run_id, user_id=user_id)
        conn.execute(
            """
            INSERT INTO user_state_history(
                run_id, event_id, replay_order, user_id, state_version,
                raw_event_count_before, raw_event_count_after,
                positive_event_count_before, positive_event_count_after,
                active_event_count_before, active_event_count_after,
                last_raw_event_id, positive_projection_json, changed_raw_event_ids_json,
                recorded_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                int(event_id),
                _none_or_int(replay_order),
                user_id,
                state_version,
                None if previous is None else _none_or_int(previous["raw_event_count_after"]),
                counts["raw"],
                None if previous is None else _none_or_int(previous["positive_event_count_after"]),
                counts["positive"],
                None if previous is None else _none_or_int(previous["active_event_count_after"]),
                counts["active"],
                counts["last_raw_event_id"],
                _json(positive_projection),
                _json(changed_ids),
                local_timestamp(),
                _json(payload or state_payload),
            ),
        )
    return state_version


def record_embedding_changes(
    db_path: Path,
    *,
    run_id: str,
    event_id: int,
    replay_order: int | None,
    user_id: int,
    rows: list[dict[str, Any]],
    active_before_count: int | None,
    active_after_count: int | None,
    state_version: str | None,
) -> None:
    if not rows:
        return
    init_store(db_path)
    with connect(db_path) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO embedding_change_history(
                    run_id, event_id, replay_order, user_id, raw_event_id, event_idx, movie_id,
                    change_type, history_len, context_start_idx, signature_hash,
                    active_before_count, active_after_count, state_version, recorded_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(event_id),
                    _none_or_int(replay_order),
                    int(user_id),
                    int(row["rawEventId"]),
                    _none_or_int(row.get("eventIdx")),
                    _none_or_int(row.get("movieId")),
                    str(row.get("changeType", "unknown")),
                    _none_or_int(row.get("historyLen")),
                    _none_or_int(row.get("contextStartIdx")),
                    row.get("signatureHash"),
                    _none_or_int(active_before_count),
                    _none_or_int(active_after_count),
                    state_version,
                    local_timestamp(),
                    _json({key: value for key, value in row.items() if key != "embedding"}),
                ),
            )


def record_assignment_decisions(
    db_path: Path,
    *,
    run_id: str,
    event_id: int,
    replay_order: int | None,
    records: list[dict[str, Any]],
    state_version: str | None = None,
) -> None:
    if not records:
        return
    init_store(db_path)
    with connect(db_path) as conn:
        for record in records:
            conn.execute(
                """
                INSERT INTO assignment_decision_history(
                    run_id, event_id, replay_order, user_id, raw_event_id, event_idx, movie_id,
                    status, interest_id, similarity, reason, state_version, recorded_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(event_id),
                    _none_or_int(replay_order),
                    int(record["userId"]),
                    int(record["rawEventId"]),
                    _none_or_int(record.get("eventIdx")),
                    _none_or_int(record.get("movieId")),
                    str(record["status"]),
                    _none_or_int(record.get("assignedInterestId")),
                    _none_or_float(record.get("similarity")),
                    record.get("reason"),
                    state_version,
                    str(record.get("recordedAt", local_timestamp())),
                    _json(record),
                ),
            )


def record_assignment_projections(
    db_path: Path,
    *,
    run_id: str,
    event_id: int,
    replay_order: int | None,
    rows: list[dict[str, Any]],
    state_version: str | None = None,
) -> None:
    if not rows:
        return
    init_store(db_path)
    with connect(db_path) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO assignment_projection_history(
                    run_id, event_id, replay_order, user_id, raw_event_id, event_idx, movie_id,
                    assignment_status, visual_status, interest_id, candidate_interest_id,
                    similarity, reason, umap_x, umap_y, base_state_version,
                    base_refit_event_id, base_refit_replay_order, projection_source,
                    state_version, recorded_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(event_id),
                    _none_or_int(replay_order),
                    int(row["userId"]),
                    int(row["rawEventId"]),
                    _none_or_int(row.get("eventIdx")),
                    _none_or_int(row.get("movieId")),
                    str(row.get("assignmentStatus", "unknown")),
                    str(row.get("visualStatus", "not_projected")),
                    _none_or_int(row.get("interestId")),
                    _none_or_int(row.get("candidateInterestId")),
                    _none_or_float(row.get("similarity")),
                    row.get("reason"),
                    _none_or_float(row.get("umapX")),
                    _none_or_float(row.get("umapY")),
                    row.get("baseStateVersion"),
                    _none_or_int(row.get("baseRefitEventId")),
                    _none_or_int(row.get("baseRefitReplayOrder")),
                    row.get("projectionSource"),
                    state_version,
                    str(row.get("recordedAt", local_timestamp())),
                    _json(row),
                ),
            )


def _interest_state_payload_counts(state_payload: dict[str, Any]) -> dict[str, int]:
    return {
        "pending": len(state_payload.get("pendingRawEventIds", [])),
        "processed": len(state_payload.get("processedRawEventIds", [])),
        "interests": len(state_payload.get("interests", [])),
        "assigned_since_last_refit": int(state_payload.get("assignedSinceLastRefit", 0)),
        "outlier_since_last_refit": int(state_payload.get("outlierSinceLastRefit", 0)),
        "refit_required": _bool(state_payload.get("refitRequired", False)),
        "refit_request_open": _bool(state_payload.get("refitRequestOpen", False)),
    }


def record_user_interest_timeline(
    db_path: Path,
    *,
    run_id: str,
    event_id: int,
    replay_order: int | None,
    source_stage: str,
    state: Any,
    assignment_records: list[dict[str, Any]] | None = None,
    refit_request_id: str | None = None,
    state_version: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    init_store(db_path)
    state_payload = state.to_dict()
    user_id = int(state_payload["userId"])
    counts = _interest_state_payload_counts(state_payload)
    records = assignment_records or []
    statuses = sorted({str(record.get("status", "unknown")) for record in records})
    assignment_status = None if not statuses else statuses[0] if len(statuses) == 1 else "mixed"
    assigned_records = [record for record in records if record.get("assignedInterestId") is not None]
    interest_id = assigned_records[0].get("assignedInterestId") if len(assigned_records) == 1 else None
    similarities = [float(record["similarity"]) for record in records if record.get("similarity") is not None]
    similarity = max(similarities) if similarities else None
    with connect(db_path) as conn:
        version = state_version or _next_state_version_conn(conn, run_id=run_id, user_id=user_id)
        conn.execute(
            """
            INSERT INTO user_interest_timeline(
                run_id, event_id, replay_order, user_id, source_stage, state_version,
                pending_count, processed_count, interest_count, assigned_since_last_refit,
                outlier_since_last_refit, refit_required, refit_request_open,
                assignment_status, interest_id, similarity, refit_request_id, refit_reasons_json,
                recorded_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                int(event_id),
                _none_or_int(replay_order),
                user_id,
                source_stage,
                version,
                counts["pending"],
                counts["processed"],
                counts["interests"],
                counts["assigned_since_last_refit"],
                counts["outlier_since_last_refit"],
                counts["refit_required"],
                counts["refit_request_open"],
                assignment_status,
                _none_or_int(interest_id),
                _none_or_float(similarity),
                refit_request_id,
                _json(state_payload.get("refitReasons", [])),
                local_timestamp(),
                _json(payload or state_payload),
            ),
        )
    return version


def record_refit_lifecycle(
    db_path: Path,
    *,
    run_id: str,
    event_id: int | None,
    replay_order: int | None,
    user_id: int,
    request_id: str | None,
    status: str,
    state_version: str | None = None,
    payload: dict[str, Any] | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    init_store(db_path)
    event = payload or {}
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO refit_lifecycle_history(
                run_id, event_id, replay_order, user_id, request_id, status, state_version,
                reasons_json, backend, active_embedding_rows, interest_count, elapsed_sec,
                error_type, error_message, recorded_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _none_or_int(event_id),
                _none_or_int(replay_order),
                int(user_id),
                request_id,
                status,
                state_version,
                _json(event.get("request", {}).get("reasons", event.get("reasons", []))),
                event.get("backendSelected") or event.get("backend"),
                _none_or_int(event.get("activeEmbeddingRows")),
                _none_or_int(event.get("interestCount")),
                _none_or_float(event.get("refitElapsedSec")),
                error_type or event.get("errorType"),
                error_message or event.get("errorMessage"),
                str(event.get("recordedAt", local_timestamp())),
                _json(event),
            ),
        )


def record_interest_vectors(
    db_path: Path,
    *,
    run_id: str,
    event_id: int | None,
    replay_order: int | None,
    user_id: int,
    request_id: str | None,
    state_version: str,
    state: Any,
) -> None:
    init_store(db_path)
    state_payload = state.to_dict()
    with connect(db_path) as conn:
        for interest in state_payload.get("interests", []):
            vector = np.asarray(interest.get("vector", []), dtype=np.float32)
            conn.execute(
                """
                INSERT INTO interest_vector_history(
                    run_id, event_id, replay_order, user_id, request_id, state_version,
                    interest_id, dim, dtype, vector_blob, assigned_count, source,
                    top_genres_json, recorded_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    _none_or_int(event_id),
                    _none_or_int(replay_order),
                    int(user_id),
                    request_id,
                    state_version,
                    int(interest["interestId"]),
                    int(vector.shape[0]),
                    "float32",
                    vector.tobytes(),
                    _none_or_int(interest.get("assignedCount")),
                    interest.get("source"),
                    _json(interest.get("topGenres", [])),
                    local_timestamp(),
                    _json(interest),
                ),
            )


def build_membership_rows(
    active_rows: list[dict[str, Any]],
    *,
    labels: np.ndarray,
    z_cluster: np.ndarray | None,
    label_to_interest_id: dict[int, int],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    labels = np.asarray(labels, dtype=np.int64)
    for idx, row in enumerate(active_rows):
        cluster_label = int(labels[idx])
        interest_id = label_to_interest_id.get(cluster_label)
        membership_source = "noise" if cluster_label == -1 else "cluster"
        point: dict[str, Any] = {
            "rawEventId": int(row["rawEventId"]),
            "eventIdx": _none_or_int(row.get("eventIdx")),
            "movieId": _none_or_int(row.get("movieId")),
            "interestId": interest_id,
            "clusterLabel": cluster_label,
            "membershipSource": membership_source,
            "recordedAt": local_timestamp(),
        }
        if z_cluster is not None and idx < len(z_cluster) and np.asarray(z_cluster).ndim == 2:
            z_row = np.asarray(z_cluster[idx])
            if z_row.shape[0] >= 2:
                point["umapX"] = float(z_row[0])
                point["umapY"] = float(z_row[1])
        output.append(point)
    return output


def record_interest_memberships(
    db_path: Path,
    *,
    run_id: str,
    event_id: int | None,
    replay_order: int | None,
    user_id: int,
    request_id: str | None,
    state_version: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    init_store(db_path)
    with connect(db_path) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO interest_membership_history(
                    run_id, event_id, replay_order, user_id, request_id, state_version,
                    raw_event_id, event_idx, movie_id, interest_id, cluster_label,
                    umap_x, umap_y, membership_source, recorded_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    _none_or_int(event_id),
                    _none_or_int(replay_order),
                    int(user_id),
                    request_id,
                    state_version,
                    int(row["rawEventId"]),
                    _none_or_int(row.get("eventIdx")),
                    _none_or_int(row.get("movieId")),
                    _none_or_int(row.get("interestId")),
                    int(row["clusterLabel"]),
                    _none_or_float(row.get("umapX")),
                    _none_or_float(row.get("umapY")),
                    row.get("membershipSource"),
                    str(row.get("recordedAt", local_timestamp())),
                    _json(row),
                ),
            )


def record_recommendations(
    db_path: Path,
    *,
    run_id: str,
    recommendation_run_id: str,
    records: list[dict[str, Any]],
    event_id: int | None,
    replay_order: int | None,
    target_user_ids: list[int],
    top_k: int,
    normalize: bool,
    include_seen: bool,
    trigger_state_versions: dict[int, str | None] | None = None,
    status: str = "completed",
) -> None:
    init_store(db_path)
    trigger_versions = trigger_state_versions or {}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(int(record["userId"]), []).append(record)
    users = sorted({int(value) for value in target_user_ids} | set(grouped))
    with connect(db_path) as conn:
        for user_id in users:
            user_rows = sorted(grouped.get(user_id, []), key=lambda item: int(item["rank"]))
            conn.execute(
                """
                INSERT INTO recommendation_history(
                    recommendation_run_id, run_id, event_id, replay_order, user_id,
                    trigger_event_id, trigger_replay_order, trigger_state_version,
                    trigger_source_stage, top_k, normalize, include_seen, row_count,
                    status, recorded_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recommendation_run_id,
                    run_id,
                    _none_or_int(event_id),
                    _none_or_int(replay_order),
                    user_id,
                    _none_or_int(event_id),
                    _none_or_int(replay_order),
                    trigger_versions.get(user_id),
                    "recommend_online",
                    int(top_k),
                    _bool(normalize),
                    _bool(include_seen),
                    len(user_rows),
                    status,
                    local_timestamp(),
                    _json({"targetUserId": user_id, "rowCount": len(user_rows)}),
                ),
            )
            for record in user_rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO recommendation_row_history(
                        recommendation_run_id, run_id, event_id, replay_order, user_id,
                        rank, movie_id, score, src_cluster, item_idx, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recommendation_run_id,
                        run_id,
                        _none_or_int(event_id),
                        _none_or_int(replay_order),
                        user_id,
                        int(record["rank"]),
                        int(record["movieId"]),
                        _none_or_float(record.get("score")),
                        _none_or_int(record.get("bestClusterId")),
                        _none_or_int(record.get("itemIdx")),
                        _json(record),
                    ),
                )
