from __future__ import annotations

import hashlib
import gzip
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np

from model.common.runtime import local_timestamp


SCHEMA_VERSION = 1
USER_STATE_PAYLOAD_ENCODING = "gzip_json_v1"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _compressed_json_payload(value: Any) -> bytes:
    return gzip.compress(_json(value).encode("utf-8"), compresslevel=1)


def _decode_user_state_payload(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    encoding = str(row["payload_encoding"]) if "payload_encoding" in keys and row["payload_encoding"] else "json"
    payload_blob = row["payload_blob"] if "payload_blob" in keys else None
    if encoding == USER_STATE_PAYLOAD_ENCODING and payload_blob is not None:
        return json.loads(gzip.decompress(bytes(payload_blob)).decode("utf-8"))
    return json.loads(str(row["payload_json"]))


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _none_or_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _none_or_int(value: Any) -> int | None:
    return None if value is None else int(value)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_store(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                speed REAL,
                output_root TEXT,
                summary_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                dtype TEXT,
                shape_json TEXT,
                row_count INTEGER,
                size_bytes INTEGER,
                sha256 TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS input_events (
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                replay_order INTEGER,
                user_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                rating REAL NOT NULL,
                rated_at TEXT NOT NULL,
                rated_at_ts REAL NOT NULL,
                source TEXT,
                payload_json TEXT,
                PRIMARY KEY (run_id, event_id)
            );

            CREATE TABLE IF NOT EXISTS event_progress (
                run_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                scheduled_at TEXT,
                emitted_at TEXT,
                processing_started_at TEXT,
                processed_at TEXT,
                injector_lag_sec REAL,
                processing_lag_sec REAL,
                end_to_end_lag_sec REAL,
                behind_schedule INTEGER NOT NULL DEFAULT 0,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, event_id)
            );

            CREATE TABLE IF NOT EXISTS stage_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id INTEGER,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                latency_sec REAL,
                attempt_no INTEGER NOT NULL DEFAULT 1,
                command_json TEXT,
                error_type TEXT,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS runtime_metrics (
                metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                event_id INTEGER,
                queue_depth INTEGER,
                refit_backlog INTEGER,
                open_refit_count INTEGER,
                running_refit_count INTEGER,
                throughput_events_per_sec REAL,
                notes_json TEXT
            );

            CREATE TABLE IF NOT EXISTS user_states (
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                version TEXT,
                status TEXT,
                raw_event_count INTEGER,
                positive_event_count INTEGER,
                active_event_count INTEGER,
                skipped_unknown_items INTEGER,
                last_raw_event_id INTEGER,
                payload_json TEXT NOT NULL,
                state_path TEXT,
                updated_at TEXT,
                PRIMARY KEY (run_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS user_raw_events (
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                raw_event_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                rating REAL NOT NULL,
                rated_at TEXT NOT NULL,
                rated_at_ts REAL NOT NULL,
                status TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, user_id, raw_event_id)
            );

            CREATE TABLE IF NOT EXISTS user_positive_events (
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                raw_event_id INTEGER NOT NULL,
                event_idx INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                rating REAL NOT NULL,
                rated_at TEXT NOT NULL,
                rated_at_ts REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                z_score REAL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, user_id, raw_event_id)
            );

            CREATE TABLE IF NOT EXISTS interest_states (
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                version TEXT,
                interest_count INTEGER NOT NULL,
                pending_count INTEGER NOT NULL,
                processed_count INTEGER NOT NULL,
                assigned_since_last_refit INTEGER NOT NULL,
                outlier_since_last_refit INTEGER NOT NULL,
                refit_required INTEGER NOT NULL,
                refit_request_open INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                state_path TEXT,
                updated_at TEXT,
                PRIMARY KEY (run_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS interest_vectors (
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                interest_id INTEGER NOT NULL,
                version TEXT,
                dim INTEGER NOT NULL,
                dtype TEXT NOT NULL,
                vector_blob BLOB NOT NULL,
                assigned_count INTEGER,
                source TEXT,
                top_genres_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (run_id, user_id, interest_id)
            );

            CREATE TABLE IF NOT EXISTS assignments (
                run_id TEXT NOT NULL,
                event_id INTEGER,
                user_id INTEGER NOT NULL,
                raw_event_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                interest_id INTEGER,
                similarity REAL,
                already_processed INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, user_id, raw_event_id, status, created_at)
            );

            CREATE TABLE IF NOT EXISTS refit_requests (
                request_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                running_at TEXT,
                closed_at TEXT,
                reasons_json TEXT,
                pending_count INTEGER,
                assigned_since_last_refit INTEGER,
                outlier_since_last_refit INTEGER,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                superseded_by TEXT,
                error_type TEXT,
                error_message TEXT,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS refit_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                latency_sec REAL,
                active_embedding_rows INTEGER,
                interest_count INTEGER,
                backend TEXT,
                skip_reason TEXT,
                error_type TEXT,
                error_message TEXT,
                payload_json TEXT
            );

            CREATE TABLE IF NOT EXISTS embedding_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                stage_attempt_id INTEGER,
                kind TEXT NOT NULL,
                scope TEXT,
                store_mode TEXT NOT NULL,
                artifact_id TEXT,
                path TEXT,
                row_count INTEGER NOT NULL,
                dim INTEGER NOT NULL,
                dtype TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS embedding_rows (
                snapshot_id TEXT NOT NULL,
                row_idx INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                raw_event_id INTEGER,
                event_idx INTEGER,
                movie_id INTEGER,
                status TEXT,
                history_len INTEGER,
                context_start_idx INTEGER,
                already_processed INTEGER NOT NULL DEFAULT 0,
                artifact_id TEXT,
                artifact_row_idx INTEGER,
                PRIMARY KEY (snapshot_id, row_idx)
            );

            CREATE TABLE IF NOT EXISTS recommendation_runs (
                recommendation_run_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                event_id INTEGER,
                user_id INTEGER,
                top_k INTEGER NOT NULL,
                normalize INTEGER NOT NULL,
                include_seen INTEGER NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                latency_sec REAL,
                row_count INTEGER NOT NULL,
                status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recommendation_rows (
                recommendation_run_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                item_idx INTEGER,
                score REAL,
                best_interest_id INTEGER,
                metadata_json TEXT,
                PRIMARY KEY (recommendation_run_id, user_id, rank)
            );

            CREATE INDEX IF NOT EXISTS idx_event_progress_run_status
                ON event_progress(run_id, status);
            CREATE INDEX IF NOT EXISTS idx_stage_attempts_run_stage
                ON stage_attempts(run_id, stage, status);
            CREATE INDEX IF NOT EXISTS idx_refit_requests_run_status
                ON refit_requests(run_id, status);
            CREATE INDEX IF NOT EXISTS idx_embedding_rows_run_user_raw
                ON embedding_rows(run_id, user_id, raw_event_id);
            """
        )
        _ensure_column(conn, "user_states", "payload_encoding", "TEXT NOT NULL DEFAULT 'json'")
        _ensure_column(conn, "user_states", "payload_blob", "BLOB")
        now = local_timestamp()
        conn.execute(
            """
            INSERT INTO schema_meta(key, value, updated_at)
            VALUES ('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (str(SCHEMA_VERSION), now),
        )


def checkpoint(db_path: Path) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        return
    init_store(db_path)
    with connect(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def upsert_run(
    db_path: Path,
    *,
    run_id: str,
    status: str,
    speed: float | None = None,
    output_root: str | None = None,
    summary_path: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> None:
    init_store(db_path)
    now = local_timestamp()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs(run_id, status, started_at, ended_at, speed, output_root, summary_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                started_at=COALESCE(excluded.started_at, runs.started_at),
                ended_at=COALESCE(excluded.ended_at, runs.ended_at),
                speed=COALESCE(excluded.speed, runs.speed),
                output_root=COALESCE(excluded.output_root, runs.output_root),
                summary_path=COALESCE(excluded.summary_path, runs.summary_path),
                updated_at=excluded.updated_at
            """,
            (run_id, status, started_at, ended_at, speed, output_root, summary_path, now, now),
        )


def record_artifact(
    db_path: Path,
    *,
    run_id: str,
    kind: str,
    path: str,
    dtype: str | None = None,
    shape: Any | None = None,
    row_count: int | None = None,
    sha256: str | None = None,
) -> str:
    init_store(db_path)
    artifact_id = f"{run_id}:{kind}:{path}"
    size_bytes = None
    try:
        file_path = Path(path)
        if file_path.exists():
            size_bytes = file_path.stat().st_size
    except OSError:
        size_bytes = None
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO artifacts(artifact_id, run_id, kind, path, dtype, shape_json, row_count, size_bytes, sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                dtype=excluded.dtype,
                shape_json=excluded.shape_json,
                row_count=excluded.row_count,
                size_bytes=excluded.size_bytes,
                sha256=excluded.sha256,
                created_at=excluded.created_at
            """,
            (
                artifact_id,
                run_id,
                kind,
                path,
                dtype,
                None if shape is None else _json(shape),
                _none_or_int(row_count),
                size_bytes,
                sha256,
                local_timestamp(),
            ),
        )
    return artifact_id


def record_input_event(db_path: Path, *, run_id: str, event: dict[str, Any]) -> None:
    init_store(db_path)
    event_id = int(event["eventId"])
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO input_events(
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
                event_id,
                _none_or_int(event.get("replayOrder")),
                int(event["userId"]),
                int(event["movieId"]),
                float(event["rating"]),
                str(event["ratedAt"]),
                float(event["ratedAtTs"]),
                event.get("source"),
                _json(event),
            ),
        )


def record_event_progress(
    db_path: Path,
    *,
    run_id: str,
    event_id: int,
    status: str,
    scheduled_at: str | None = None,
    emitted_at: str | None = None,
    processing_started_at: str | None = None,
    processed_at: str | None = None,
    injector_lag_sec: float | None = None,
    processing_lag_sec: float | None = None,
    end_to_end_lag_sec: float | None = None,
    behind_schedule: bool | None = None,
    failed_attempts: int | None = None,
) -> None:
    init_store(db_path)
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT failed_attempts, behind_schedule FROM event_progress WHERE run_id=? AND event_id=?",
            (run_id, int(event_id)),
        ).fetchone()
        current_failed = 0 if existing is None else int(existing["failed_attempts"])
        current_behind_schedule = 0 if existing is None else int(existing["behind_schedule"])
        conn.execute(
            """
            INSERT INTO event_progress(
                run_id, event_id, status, scheduled_at, emitted_at, processing_started_at, processed_at,
                injector_lag_sec, processing_lag_sec, end_to_end_lag_sec, behind_schedule, failed_attempts, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, event_id) DO UPDATE SET
                status=excluded.status,
                scheduled_at=COALESCE(excluded.scheduled_at, event_progress.scheduled_at),
                emitted_at=COALESCE(excluded.emitted_at, event_progress.emitted_at),
                processing_started_at=COALESCE(excluded.processing_started_at, event_progress.processing_started_at),
                processed_at=COALESCE(excluded.processed_at, event_progress.processed_at),
                injector_lag_sec=COALESCE(excluded.injector_lag_sec, event_progress.injector_lag_sec),
                processing_lag_sec=COALESCE(excluded.processing_lag_sec, event_progress.processing_lag_sec),
                end_to_end_lag_sec=COALESCE(excluded.end_to_end_lag_sec, event_progress.end_to_end_lag_sec),
                behind_schedule=excluded.behind_schedule,
                failed_attempts=excluded.failed_attempts,
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                int(event_id),
                status,
                scheduled_at,
                emitted_at,
                processing_started_at,
                processed_at,
                _none_or_float(injector_lag_sec),
                _none_or_float(processing_lag_sec),
                _none_or_float(end_to_end_lag_sec),
                current_behind_schedule if behind_schedule is None else _bool(behind_schedule),
                current_failed if failed_attempts is None else int(failed_attempts),
                local_timestamp(),
            ),
        )


def start_stage_attempt(
    db_path: Path,
    *,
    run_id: str,
    stage: str,
    event_id: int | None = None,
    command: list[str] | None = None,
) -> int:
    init_store(db_path)
    with connect(db_path) as conn:
        if event_id is None:
            existing = conn.execute(
                "SELECT COUNT(*) AS n FROM stage_attempts WHERE run_id=? AND event_id IS NULL AND stage=?",
                (run_id, stage),
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT COUNT(*) AS n FROM stage_attempts WHERE run_id=? AND event_id=? AND stage=?",
                (run_id, int(event_id), stage),
            ).fetchone()
        attempt_no = int(existing["n"]) + 1
        cursor = conn.execute(
            """
            INSERT INTO stage_attempts(run_id, event_id, stage, status, started_at, attempt_no, command_json)
            VALUES (?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                run_id,
                _none_or_int(event_id),
                stage,
                local_timestamp(),
                attempt_no,
                None if command is None else _json(command),
            ),
        )
        return int(cursor.lastrowid)


def finish_stage_attempt(
    db_path: Path,
    *,
    attempt_id: int,
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    init_store(db_path)
    ended_at = local_timestamp()
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT started_at FROM stage_attempts WHERE attempt_id=?",
            (int(attempt_id),),
        ).fetchone()
        latency = None
        if row is not None:
            try:
                # local_timestamp is ISO; wall-clock exactness is not critical here.
                from datetime import datetime

                started = datetime.fromisoformat(str(row["started_at"]))
                ended = datetime.fromisoformat(ended_at)
                latency = (ended - started).total_seconds()
            except Exception:
                latency = None
        conn.execute(
            """
            UPDATE stage_attempts
            SET status=?, ended_at=?, latency_sec=?, error_type=?, error_message=?
            WHERE attempt_id=?
            """,
            (status, ended_at, latency, error_type, error_message, int(attempt_id)),
        )


def record_runtime_metric(
    db_path: Path,
    *,
    run_id: str,
    event_id: int | None = None,
    queue_depth: int | None = None,
    refit_backlog: int | None = None,
    throughput_events_per_sec: float | None = None,
    notes: dict[str, Any] | None = None,
) -> None:
    init_store(db_path)
    with connect(db_path) as conn:
        open_refit = conn.execute(
            "SELECT COUNT(*) AS n FROM refit_requests WHERE run_id=? AND status='open'",
            (run_id,),
        ).fetchone()["n"]
        running_refit = conn.execute(
            "SELECT COUNT(*) AS n FROM refit_requests WHERE run_id=? AND status='running'",
            (run_id,),
        ).fetchone()["n"]
        conn.execute(
            """
            INSERT INTO runtime_metrics(
                run_id, recorded_at, event_id, queue_depth, refit_backlog,
                open_refit_count, running_refit_count, throughput_events_per_sec, notes_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                local_timestamp(),
                _none_or_int(event_id),
                _none_or_int(queue_depth),
                _none_or_int(refit_backlog),
                int(open_refit),
                int(running_refit),
                _none_or_float(throughput_events_per_sec),
                None if notes is None else _json(notes),
            ),
        )


def record_user_state_conn(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    state: Any,
    state_path: str | None = None,
    include_event_rows: bool = True,
) -> None:
    payload = state.to_dict()
    stats = payload.get("stats", {})
    raw_events = payload.get("rawEvents", [])
    positive_events = payload.get("positiveEvents", [])
    active_events = [event for event in positive_events if event.get("status") == "active"]
    skipped_unknown = [event for event in positive_events if event.get("status") == "skipped_unknown"]
    last_raw_event_id = max((int(event["rawEventId"]) for event in raw_events), default=None)
    payload_blob = _compressed_json_payload(payload)
    payload_marker = _json(
        {
            "encoding": USER_STATE_PAYLOAD_ENCODING,
            "compressedBytes": len(payload_blob),
        }
    )

    conn.execute(
        """
        INSERT INTO user_states(
            run_id, user_id, version, status, raw_event_count, positive_event_count, active_event_count,
            skipped_unknown_items, last_raw_event_id, payload_json, payload_encoding, payload_blob,
            state_path, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, user_id) DO UPDATE SET
            version=excluded.version,
            status=excluded.status,
            raw_event_count=excluded.raw_event_count,
            positive_event_count=excluded.positive_event_count,
            active_event_count=excluded.active_event_count,
            skipped_unknown_items=excluded.skipped_unknown_items,
            last_raw_event_id=excluded.last_raw_event_id,
            payload_json=excluded.payload_json,
            payload_encoding=excluded.payload_encoding,
            payload_blob=excluded.payload_blob,
            state_path=excluded.state_path,
            updated_at=excluded.updated_at
        """,
        (
            run_id,
            int(payload["userId"]),
            payload.get("version"),
            "active",
            int(stats.get("rawEventCount", len(raw_events))),
            int(stats.get("positiveEventCount", len(positive_events))),
            int(stats.get("activeEventCount", len(active_events))),
            int(stats.get("skippedUnknownItems", len(skipped_unknown))),
            last_raw_event_id,
            payload_marker,
            USER_STATE_PAYLOAD_ENCODING,
            payload_blob,
            state_path,
            payload.get("updatedAt"),
        ),
    )
    conn.execute("DELETE FROM user_raw_events WHERE run_id=? AND user_id=?", (run_id, int(payload["userId"])))
    conn.execute("DELETE FROM user_positive_events WHERE run_id=? AND user_id=?", (run_id, int(payload["userId"])))
    if not include_event_rows:
        return

    for event in raw_events:
        conn.execute(
            """
            INSERT INTO user_raw_events(
                run_id, user_id, raw_event_id, movie_id, rating, rated_at, rated_at_ts, status, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, user_id, raw_event_id) DO UPDATE SET
                movie_id=excluded.movie_id,
                rating=excluded.rating,
                rated_at=excluded.rated_at,
                rated_at_ts=excluded.rated_at_ts,
                status=excluded.status,
                payload_json=excluded.payload_json
            """,
            (
                run_id,
                int(event["userId"]),
                int(event["rawEventId"]),
                int(event["movieId"]),
                float(event["rating"]),
                str(event["ratedAt"]),
                float(event["ratedAtTs"]),
                "raw",
                _json(event),
            ),
        )
    for event in positive_events:
        conn.execute(
            """
            INSERT INTO user_positive_events(
                run_id, user_id, raw_event_id, event_idx, movie_id, rating, rated_at, rated_at_ts,
                status, reason, z_score, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, user_id, raw_event_id) DO UPDATE SET
                event_idx=excluded.event_idx,
                movie_id=excluded.movie_id,
                rating=excluded.rating,
                rated_at=excluded.rated_at,
                rated_at_ts=excluded.rated_at_ts,
                status=excluded.status,
                reason=excluded.reason,
                z_score=excluded.z_score,
                payload_json=excluded.payload_json
            """,
            (
                run_id,
                int(event["userId"]),
                int(event["rawEventId"]),
                int(event["eventIdx"]),
                int(event["movieId"]),
                float(event["rating"]),
                str(event["ratedAt"]),
                float(event["ratedAtTs"]),
                str(event["status"]),
                event.get("positiveReason"),
                _none_or_float(event.get("zScore")),
                _json(event),
            ),
        )


def record_user_state(
    db_path: Path,
    *,
    run_id: str,
    state: Any,
    state_path: str | None = None,
    include_event_rows: bool = True,
) -> None:
    init_store(db_path)
    with connect(db_path) as conn:
        record_user_state_conn(
            conn,
            run_id=run_id,
            state=state,
            state_path=state_path,
            include_event_rows=include_event_rows,
        )


def fetch_user_state_payload(db_path: Path, *, run_id: str, user_id: int) -> dict[str, Any] | None:
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    init_store(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json, payload_encoding, payload_blob FROM user_states WHERE run_id=? AND user_id=?",
            (run_id, int(user_id)),
        ).fetchone()
    if row is None:
        return None
    return _decode_user_state_payload(row)


def list_user_state_ids(db_path: Path, *, run_id: str) -> list[int]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    init_store(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id FROM user_states WHERE run_id=? ORDER BY user_id",
            (run_id,),
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def record_interest_state_conn(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    state: Any,
    state_path: str | None = None,
) -> None:
    payload = state.to_dict()
    interests = payload.get("interests", [])
    user_id = int(payload["userId"])
    conn.execute(
        """
        INSERT INTO interest_states(
            run_id, user_id, version, interest_count, pending_count, processed_count,
            assigned_since_last_refit, outlier_since_last_refit, refit_required, refit_request_open,
            payload_json, state_path, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, user_id) DO UPDATE SET
            version=excluded.version,
            interest_count=excluded.interest_count,
            pending_count=excluded.pending_count,
            processed_count=excluded.processed_count,
            assigned_since_last_refit=excluded.assigned_since_last_refit,
            outlier_since_last_refit=excluded.outlier_since_last_refit,
            refit_required=excluded.refit_required,
            refit_request_open=excluded.refit_request_open,
            payload_json=excluded.payload_json,
            state_path=excluded.state_path,
            updated_at=excluded.updated_at
        """,
        (
            run_id,
            user_id,
            payload.get("version"),
            len(interests),
            len(payload.get("pendingRawEventIds", [])),
            len(payload.get("processedRawEventIds", [])),
            int(payload.get("assignedSinceLastRefit", 0)),
            int(payload.get("outlierSinceLastRefit", 0)),
            _bool(payload.get("refitRequired", False)),
            _bool(payload.get("refitRequestOpen", False)),
            _json(payload),
            state_path,
            payload.get("updatedAt"),
        ),
    )
    conn.execute("DELETE FROM interest_vectors WHERE run_id=? AND user_id=?", (run_id, user_id))
    for interest in interests:
        vector = np.asarray(interest.get("vector", []), dtype=np.float32)
        conn.execute(
            """
            INSERT INTO interest_vectors(
                run_id, user_id, interest_id, version, dim, dtype, vector_blob, assigned_count,
                source, top_genres_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                user_id,
                int(interest["interestId"]),
                payload.get("version"),
                int(vector.shape[0]),
                "float32",
                vector.tobytes(),
                int(interest.get("assignedCount", 0)),
                interest.get("source"),
                _json(interest.get("topGenres", [])),
                interest.get("createdAt"),
                interest.get("updatedAt"),
            ),
        )


def record_interest_state(db_path: Path, *, run_id: str, state: Any, state_path: str | None = None) -> None:
    init_store(db_path)
    with connect(db_path) as conn:
        record_interest_state_conn(conn, run_id=run_id, state=state, state_path=state_path)


def fetch_interest_state_payload_conn(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    user_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json FROM interest_states WHERE run_id=? AND user_id=?",
        (run_id, int(user_id)),
    ).fetchone()
    if row is None:
        return None
    return json.loads(str(row["payload_json"]))


def fetch_interest_state_payload(db_path: Path, *, run_id: str, user_id: int) -> dict[str, Any] | None:
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    init_store(db_path)
    with connect(db_path) as conn:
        return fetch_interest_state_payload_conn(conn, run_id=run_id, user_id=user_id)


def list_interest_state_ids(db_path: Path, *, run_id: str) -> list[int]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    init_store(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id FROM interest_states WHERE run_id=? ORDER BY user_id",
            (run_id,),
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def count_state_rows(db_path: Path, *, run_id: str) -> dict[str, int]:
    db_path = Path(db_path)
    if not db_path.exists():
        return {
            "userStates": 0,
            "userRawEvents": 0,
            "userPositiveEvents": 0,
            "interestStates": 0,
            "interestVectors": 0,
        }
    init_store(db_path)
    with connect(db_path) as conn:
        return {
            "userStates": int(
                conn.execute("SELECT COUNT(*) AS n FROM user_states WHERE run_id=?", (run_id,)).fetchone()["n"]
            ),
            "userRawEvents": int(
                conn.execute("SELECT COUNT(*) AS n FROM user_raw_events WHERE run_id=?", (run_id,)).fetchone()["n"]
            ),
            "userPositiveEvents": int(
                conn.execute("SELECT COUNT(*) AS n FROM user_positive_events WHERE run_id=?", (run_id,)).fetchone()[
                    "n"
                ]
            ),
            "interestStates": int(
                conn.execute("SELECT COUNT(*) AS n FROM interest_states WHERE run_id=?", (run_id,)).fetchone()["n"]
            ),
            "interestVectors": int(
                conn.execute("SELECT COUNT(*) AS n FROM interest_vectors WHERE run_id=?", (run_id,)).fetchone()["n"]
            ),
        }


def record_assignments(
    db_path: Path,
    *,
    run_id: str,
    records: list[dict[str, Any]],
    event_id: int | None = None,
) -> None:
    if not records:
        return
    init_store(db_path)
    with connect(db_path) as conn:
        for record in records:
            conn.execute(
                """
                INSERT OR REPLACE INTO assignments(
                    run_id, event_id, user_id, raw_event_id, movie_id, status, interest_id, similarity,
                    already_processed, reason, created_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    _none_or_int(event_id),
                    int(record["userId"]),
                    int(record["rawEventId"]),
                    int(record["movieId"]),
                    str(record["status"]),
                    _none_or_int(record.get("assignedInterestId")),
                    _none_or_float(record.get("similarity")),
                    _bool(record.get("status") == "already_processed"),
                    record.get("reason"),
                    str(record.get("recordedAt", local_timestamp())),
                    _json(record),
                ),
            )


def _request_id(run_id: str, record: dict[str, Any]) -> str:
    opened_at = str(record.get("recordedAt", local_timestamp()))
    digest = hashlib.sha1(_json(record).encode("utf-8")).hexdigest()[:12]
    return f"{run_id}:{int(record['userId'])}:{opened_at}:{digest}"


def open_refit_requests(db_path: Path, *, run_id: str, records: list[dict[str, Any]]) -> list[str]:
    if not records:
        return []
    init_store(db_path)
    request_ids: list[str] = []
    with connect(db_path) as conn:
        for record in records:
            user_id = int(record["userId"])
            request_id = _request_id(run_id, record)
            now = str(record.get("recordedAt", local_timestamp()))
            conn.execute(
                """
                UPDATE refit_requests
                SET status='superseded', closed_at=?, superseded_by=?
                WHERE run_id=? AND user_id=? AND status='open' AND request_id<>?
                """,
                (now, request_id, run_id, user_id, request_id),
            )
            conn.execute(
                """
                INSERT INTO refit_requests(
                    request_id, run_id, user_id, status, opened_at, reasons_json, pending_count,
                    assigned_since_last_refit, outlier_since_last_refit, payload_json
                )
                VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    status='open',
                    reasons_json=excluded.reasons_json,
                    pending_count=excluded.pending_count,
                    assigned_since_last_refit=excluded.assigned_since_last_refit,
                    outlier_since_last_refit=excluded.outlier_since_last_refit,
                    payload_json=excluded.payload_json
                WHERE refit_requests.status='open'
                """,
                (
                    request_id,
                    run_id,
                    user_id,
                    now,
                    _json(record.get("reasons", [])),
                    _none_or_int(record.get("pendingRawEventCount")),
                    _none_or_int(record.get("assignedSinceLastRefit")),
                    _none_or_int(record.get("outlierSinceLastRefit")),
                    _json(record),
                ),
            )
            request_ids.append(request_id)
    return request_ids


def claim_refit_request(db_path: Path, *, run_id: str, user_id: int) -> str | None:
    init_store(db_path)
    now = local_timestamp()
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT request_id FROM refit_requests
            WHERE run_id=? AND user_id=? AND status='open'
            ORDER BY opened_at DESC
            LIMIT 1
            """,
            (run_id, int(user_id)),
        ).fetchone()
        if row is None:
            return None
        request_id = str(row["request_id"])
        conn.execute(
            """
            UPDATE refit_requests
            SET status='running', running_at=?, attempt_count=attempt_count + 1
            WHERE request_id=?
            """,
            (now, request_id),
        )
        return request_id


def complete_refit_request(
    db_path: Path,
    *,
    run_id: str,
    user_id: int,
    status: str,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    init_store(db_path)
    if status not in {"closed", "skipped", "failed", "superseded"}:
        raise ValueError(f"Invalid refit completion status: {status}")
    now = local_timestamp()
    with connect(db_path) as conn:
        if request_id is None:
            row = conn.execute(
                """
                SELECT request_id FROM refit_requests
                WHERE run_id=? AND user_id=? AND status IN ('open', 'running')
                ORDER BY opened_at DESC
                LIMIT 1
                """,
                (run_id, int(user_id)),
            ).fetchone()
            request_id = None if row is None else str(row["request_id"])
        if request_id is not None:
            conn.execute(
                """
                UPDATE refit_requests
                SET status=?, closed_at=?, error_type=?, error_message=?, payload_json=COALESCE(?, payload_json)
                WHERE request_id=?
                """,
                (
                    status,
                    now,
                    error_type,
                    error_message,
                    None if payload is None else _json(payload),
                    request_id,
                ),
            )
        if payload is not None:
            conn.execute(
                """
                INSERT INTO refit_attempts(
                    request_id, run_id, user_id, status, started_at, ended_at, latency_sec,
                    active_embedding_rows, interest_count, backend, skip_reason, error_type, error_message, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    run_id,
                    int(user_id),
                    status,
                    payload.get("recordedAt", now),
                    now,
                    _none_or_float(payload.get("refitElapsedSec")),
                    _none_or_int(payload.get("activeEmbeddingRows")),
                    _none_or_int(payload.get("interestCount")),
                    payload.get("backendSelected"),
                    payload.get("reason"),
                    error_type,
                    error_message,
                    _json(payload),
                ),
            )


def record_embedding_snapshot(
    db_path: Path,
    *,
    run_id: str,
    kind: str,
    path: str,
    arrays: dict[str, np.ndarray],
    stage_attempt_id: int | None = None,
    scope: str = "touched_users",
) -> str:
    init_store(db_path)
    embeddings = arrays["embeddings"]
    row_count = int(embeddings.shape[0]) if embeddings.ndim >= 1 else 0
    dim = int(embeddings.shape[1]) if embeddings.ndim == 2 else 0
    artifact_id = record_artifact(
        db_path,
        run_id=run_id,
        kind=kind,
        path=path,
        dtype=str(embeddings.dtype),
        shape={"embeddings": list(embeddings.shape)},
        row_count=row_count,
    )
    snapshot_id = f"{run_id}:{kind}:{int(time.time() * 1_000_000)}"
    created_at = local_timestamp()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO embedding_snapshots(
                snapshot_id, run_id, stage_attempt_id, kind, scope, store_mode, artifact_id, path,
                row_count, dim, dtype, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                run_id,
                _none_or_int(stage_attempt_id),
                kind,
                scope,
                artifact_id,
                path,
                row_count,
                dim,
                str(embeddings.dtype),
                created_at,
            ),
        )
        user_ids = arrays.get("user_ids", np.array([], dtype=np.int64))
        raw_event_ids = arrays.get("raw_event_ids")
        event_idx = arrays.get("event_idx")
        movie_ids = arrays.get("movie_ids")
        status = arrays.get("status", np.array(["active"] * row_count))
        history_len = arrays.get("history_len")
        context_start_idx = arrays.get("context_start_idx")
        for row_idx in range(row_count):
            user_id = int(user_ids[row_idx])
            raw_event_id = None if raw_event_ids is None else int(raw_event_ids[row_idx])
            already = 0
            if raw_event_id is not None:
                prior = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM embedding_rows
                    WHERE run_id=? AND user_id=? AND raw_event_id=?
                    """,
                    (run_id, user_id, raw_event_id),
                ).fetchone()
                already = 1 if int(prior["n"]) > 0 else 0
            conn.execute(
                """
                INSERT INTO embedding_rows(
                    snapshot_id, row_idx, run_id, user_id, raw_event_id, event_idx, movie_id, status,
                    history_len, context_start_idx, already_processed, artifact_id, artifact_row_idx
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    row_idx,
                    run_id,
                    user_id,
                    raw_event_id,
                    None if event_idx is None else int(event_idx[row_idx]),
                    None if movie_ids is None else int(movie_ids[row_idx]),
                    str(status[row_idx]),
                    None if history_len is None else int(history_len[row_idx]),
                    None if context_start_idx is None else int(context_start_idx[row_idx]),
                    already,
                    artifact_id,
                    row_idx,
                ),
            )
    return snapshot_id


def record_recommendations(
    db_path: Path,
    *,
    run_id: str,
    records: list[dict[str, Any]],
    event_id: int | None = None,
    target_user_ids: list[int] | None = None,
    top_k: int,
    normalize: bool,
    include_seen: bool,
    status: str = "completed",
) -> str:
    init_store(db_path)
    now = local_timestamp()
    target_users = (
        sorted({int(value) for value in target_user_ids})
        if target_user_ids is not None
        else sorted({int(record["userId"]) for record in records})
    )
    user_part = "none" if not target_users else "-".join(str(value) for value in target_users[:5])
    recommendation_run_id = f"{run_id}:{event_id if event_id is not None else 'manual'}:{user_part}:{int(time.time() * 1_000_000)}"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO recommendation_runs(
                recommendation_run_id, run_id, event_id, user_id, top_k, normalize, include_seen,
                started_at, ended_at, latency_sec, row_count, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recommendation_run_id,
                run_id,
                _none_or_int(event_id),
                target_users[0] if len(target_users) == 1 else None,
                int(top_k),
                _bool(normalize),
                _bool(include_seen),
                now,
                now,
                0.0,
                len(records),
                status,
            ),
        )
        for record in records:
            conn.execute(
                """
                INSERT OR REPLACE INTO recommendation_rows(
                    recommendation_run_id, run_id, user_id, rank, movie_id, item_idx, score, best_interest_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recommendation_run_id,
                    run_id,
                    int(record["userId"]),
                    int(record["rank"]),
                    int(record["movieId"]),
                    _none_or_int(record.get("itemIdx")),
                    _none_or_float(record.get("score")),
                    _none_or_int(record.get("bestClusterId")),
                    _json(record),
                ),
            )
    return recommendation_run_id
