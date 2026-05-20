from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import argparse
import json

import model.stream.history_store as history_store
from model.common.runtime import local_timestamp


DASHBOARD_COMPACT_SCHEMA_VERSION = 2
NOISE_CLUSTER_ID = -1


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: str | None, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _table_columns(conn: Any, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn: Any, table: str, column: str, definition: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_store(db_path: Path) -> None:
    with history_store.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_timeline (
                user_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                movie_id INTEGER NOT NULL,
                is_refit_triggered INTEGER NOT NULL DEFAULT 0,
                refit_reason TEXT,
                k_count INTEGER NOT NULL DEFAULT 0,
                noise_count INTEGER NOT NULL DEFAULT 0,
                assigned_since_refit INTEGER NOT NULL DEFAULT 0,
                not_assigned_since_refit INTEGER NOT NULL DEFAULT 0,
                not_projected_since_refit INTEGER NOT NULL DEFAULT 0,
                last_refit_event_id INTEGER,
                PRIMARY KEY (user_id, event_id)
            );

            CREATE TABLE IF NOT EXISTS visualization_states (
                user_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                points_data TEXT NOT NULL,
                PRIMARY KEY (user_id, event_id)
            );

            CREATE TABLE IF NOT EXISTS cluster_snapshots (
                user_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                cluster_id INTEGER NOT NULL,
                size INTEGER NOT NULL,
                base_size INTEGER NOT NULL DEFAULT 0,
                assigned_size INTEGER NOT NULL DEFAULT 0,
                total_size INTEGER NOT NULL DEFAULT 0,
                top_genres TEXT,
                PRIMARY KEY (user_id, event_id, cluster_id)
            );

            CREATE TABLE IF NOT EXISTS recommendations (
                user_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                score REAL,
                src_cluster INTEGER,
                PRIMARY KEY (user_id, event_id, rank)
            );
            """
        )
        _ensure_column(conn, "event_timeline", "assigned_since_refit", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "event_timeline", "not_assigned_since_refit", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "event_timeline", "not_projected_since_refit", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "event_timeline", "last_refit_event_id", "INTEGER")
        _ensure_column(conn, "cluster_snapshots", "base_size", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "cluster_snapshots", "assigned_size", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "cluster_snapshots", "total_size", "INTEGER NOT NULL DEFAULT 0")
        now = local_timestamp()
        conn.execute(
            """
            INSERT INTO schema_meta(key, value, updated_at)
            VALUES ('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (str(DASHBOARD_COMPACT_SCHEMA_VERSION), now),
        )


def _resolve_run_id(history_db: Path, run_id: str | None) -> str:
    if run_id is not None:
        return run_id
    with history_store.connect(history_db) as conn:
        row = conn.execute(
            """
            SELECT run_id
            FROM history_runs
            ORDER BY COALESCE(started_at, created_at) DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise ValueError(f"No history run found in {history_db}")
    return str(row["run_id"])


def _latest_membership_context(conn: Any, *, run_id: str, user_id: int, replay_order: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT state_version, event_id, replay_order
        FROM interest_membership_history
        WHERE run_id=? AND user_id=? AND replay_order<=?
        ORDER BY replay_order DESC, history_id DESC
        LIMIT 1
        """,
        (run_id, int(user_id), int(replay_order)),
    ).fetchone()
    if row is None:
        return {"state_version": None, "event_id": None, "replay_order": -1, "rows": []}
    state_version = str(row["state_version"])
    rows = conn.execute(
        """
        SELECT *
        FROM interest_membership_history
        WHERE run_id=? AND user_id=? AND state_version=?
        ORDER BY event_idx, raw_event_id
        """,
        (run_id, int(user_id), state_version),
    ).fetchall()
    return {
        "state_version": state_version,
        "event_id": None if row["event_id"] is None else int(row["event_id"]),
        "replay_order": -1 if row["replay_order"] is None else int(row["replay_order"]),
        "rows": rows,
    }


def _interest_top_genres(conn: Any, *, run_id: str, user_id: int, state_version: str) -> dict[int, Any]:
    rows = conn.execute(
        """
        SELECT interest_id, top_genres_json
        FROM interest_vector_history
        WHERE run_id=? AND user_id=? AND state_version=?
        """,
        (run_id, int(user_id), state_version),
    ).fetchall()
    output: dict[int, Any] = {}
    for row in rows:
        genres = _loads(row["top_genres_json"], [])
        if genres and isinstance(genres[0], dict):
            genres = [str(item.get("genre")) for item in genres if item.get("genre") is not None]
        output[int(row["interest_id"])] = genres
    return output


def _projection_delta_events(
    conn: Any,
    *,
    run_id: str,
    user_id: int,
    after_replay_order: int,
    replay_order: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    projections = conn.execute(
        """
        SELECT *
        FROM assignment_projection_history
        WHERE run_id=? AND user_id=? AND replay_order>? AND replay_order<=?
        ORDER BY replay_order, history_id
        """,
        (run_id, int(user_id), int(after_replay_order), int(replay_order)),
    ).fetchall()
    for row in projections:
        events.append(
            {
                "kind": "projection",
                "replay_order": -1 if row["replay_order"] is None else int(row["replay_order"]),
                "history_id": int(row["history_id"]),
                "row": row,
            }
        )

    inactivations = conn.execute(
        """
        SELECT *
        FROM embedding_change_history
        WHERE run_id=? AND user_id=? AND replay_order>? AND replay_order<=? AND change_type IN ('inactive', 'inactivate')
        ORDER BY replay_order, history_id
        """,
        (run_id, int(user_id), int(after_replay_order), int(replay_order)),
    ).fetchall()
    for row in inactivations:
        events.append(
            {
                "kind": "inactivate",
                "replay_order": -1 if row["replay_order"] is None else int(row["replay_order"]),
                "history_id": int(row["history_id"]),
                "row": row,
            }
        )
    priority = {"inactivate": 0, "projection": 1}
    return sorted(events, key=lambda item: (item["replay_order"], priority[item["kind"]], item["history_id"]))


def _latest_recommendation_rows(conn: Any, *, run_id: str, user_id: int, replay_order: int) -> list[Any]:
    row = conn.execute(
        """
        SELECT recommendation_run_id
        FROM recommendation_history
        WHERE run_id=? AND user_id=? AND trigger_replay_order<=?
        ORDER BY trigger_replay_order DESC, history_id DESC
        LIMIT 1
        """,
        (run_id, int(user_id), int(replay_order)),
    ).fetchone()
    if row is None:
        return []
    return conn.execute(
        """
        SELECT *
        FROM recommendation_row_history
        WHERE run_id=? AND user_id=? AND recommendation_run_id=?
        ORDER BY rank
        """,
        (run_id, int(user_id), str(row["recommendation_run_id"])),
    ).fetchall()


def _refit_event(conn: Any, *, run_id: str, user_id: int, event_id: int) -> Any | None:
    return conn.execute(
        """
        SELECT *
        FROM refit_lifecycle_history
        WHERE run_id=? AND user_id=? AND event_id=?
        ORDER BY history_id DESC
        LIMIT 1
        """,
        (run_id, int(user_id), int(event_id)),
    ).fetchone()


def _membership_point(row: Any) -> dict[str, Any] | None:
    if row["umap_x"] is None or row["umap_y"] is None:
        return None
    interest_id = None if row["interest_id"] is None else int(row["interest_id"])
    cluster_label = int(row["cluster_label"])
    visual_status = "noise" if interest_id is None else "base"
    cluster_id = NOISE_CLUSTER_ID if interest_id is None else interest_id
    return {
        "raw_event_id": int(row["raw_event_id"]),
        "event_idx": None if row["event_idx"] is None else int(row["event_idx"]),
        "movie_id": None if row["movie_id"] is None else int(row["movie_id"]),
        "x": round(float(row["umap_x"]), 3),
        "y": round(float(row["umap_y"]), 3),
        "c": cluster_id,
        "cluster_id": None if interest_id is None else interest_id,
        "cluster_label": cluster_label,
        "visual_status": visual_status,
        "point_source": "refit",
    }


def _projection_point(row: Any) -> dict[str, Any] | None:
    if row["umap_x"] is None or row["umap_y"] is None:
        return None
    visual_status = str(row["visual_status"])
    if visual_status == "assigned" and row["interest_id"] is not None:
        cluster_id = int(row["interest_id"])
        c_value = cluster_id
    else:
        cluster_id = None
        c_value = NOISE_CLUSTER_ID
    return {
        "raw_event_id": int(row["raw_event_id"]),
        "event_idx": None if row["event_idx"] is None else int(row["event_idx"]),
        "movie_id": None if row["movie_id"] is None else int(row["movie_id"]),
        "x": round(float(row["umap_x"]), 3),
        "y": round(float(row["umap_y"]), 3),
        "c": c_value,
        "cluster_id": cluster_id,
        "candidate_cluster_id": None if row["candidate_interest_id"] is None else int(row["candidate_interest_id"]),
        "visual_status": visual_status,
        "assignment_status": str(row["assignment_status"]),
        "point_source": "assignment",
        "similarity": None if row["similarity"] is None else round(float(row["similarity"]), 6),
        "reason": row["reason"],
        "base_state_version": row["base_state_version"],
        "base_refit_event_id": row["base_refit_event_id"],
    }


def compact_history_to_dashboard(*, history_db: Path, output_db: Path, run_id: str | None = None) -> dict[str, Any]:
    history_db = Path(history_db)
    output_db = Path(output_db)
    history_store.init_store(history_db)
    init_store(output_db)
    resolved_run_id = _resolve_run_id(history_db, run_id)

    with history_store.connect(history_db) as source, history_store.connect(output_db) as target:
        target.execute("DELETE FROM recommendations")
        target.execute("DELETE FROM cluster_snapshots")
        target.execute("DELETE FROM visualization_states")
        target.execute("DELETE FROM event_timeline")

        events = source.execute(
            """
            SELECT *
            FROM history_run_events
            WHERE run_id=?
            ORDER BY user_id, replay_order, event_id
            """,
            (resolved_run_id,),
        ).fetchall()

        point_rows = 0
        cluster_rows = 0
        recommendation_rows = 0
        for event in events:
            user_id = int(event["user_id"])
            event_id = int(event["event_id"])
            replay_order = int(event["replay_order"])
            refit = _refit_event(source, run_id=resolved_run_id, user_id=user_id, event_id=event_id)
            refit_reason = None
            if refit is not None:
                reasons = _loads(refit["reasons_json"], [])
                refit_reason = _json(reasons) if reasons else str(refit["status"])

            context = _latest_membership_context(
                source,
                run_id=resolved_run_id,
                user_id=user_id,
                replay_order=replay_order,
            )
            state_version = context["state_version"]
            base_replay_order = int(context["replay_order"])
            points_by_raw_event_id: dict[int, dict[str, Any]] = {}
            for row in context["rows"]:
                point = _membership_point(row)
                if point is not None:
                    points_by_raw_event_id[int(point["raw_event_id"])] = point

            not_projected_since_refit = 0
            for delta in _projection_delta_events(
                source,
                run_id=resolved_run_id,
                user_id=user_id,
                after_replay_order=base_replay_order,
                replay_order=replay_order,
            ):
                row = delta["row"]
                raw_event_id = int(row["raw_event_id"])
                if delta["kind"] == "inactivate":
                    points_by_raw_event_id.pop(raw_event_id, None)
                    continue
                if str(row["visual_status"]) == "not_projected":
                    not_projected_since_refit += 1
                    continue
                point = _projection_point(row)
                if point is None:
                    not_projected_since_refit += 1
                    continue
                points_by_raw_event_id[raw_event_id] = point

            points = sorted(
                points_by_raw_event_id.values(),
                key=lambda item: (int(item.get("event_idx") or 0), int(item["raw_event_id"])),
            )
            cluster_counts: Counter[int] = Counter()
            base_cluster_counts: Counter[int] = Counter()
            assigned_cluster_counts: Counter[int] = Counter()
            noise_count = 0
            not_assigned_since_refit = 0
            assigned_since_refit = 0
            for point in points:
                visual_status = str(point.get("visual_status", "base"))
                cluster_id = point.get("cluster_id")
                if visual_status == "not_assigned":
                    not_assigned_since_refit += 1
                    continue
                if cluster_id is None:
                    noise_count += 1
                    continue
                cluster_id_int = int(cluster_id)
                cluster_counts[cluster_id_int] += 1
                if point.get("point_source") == "assignment":
                    assigned_cluster_counts[cluster_id_int] += 1
                    assigned_since_refit += 1
                else:
                    base_cluster_counts[cluster_id_int] += 1

            top_genres_by_interest = (
                {}
                if state_version is None
                else _interest_top_genres(
                    source,
                    run_id=resolved_run_id,
                    user_id=user_id,
                    state_version=state_version,
                )
            )

            target.execute(
                """
                INSERT OR REPLACE INTO event_timeline(
                    user_id, event_id, timestamp, movie_id, is_refit_triggered,
                    refit_reason, k_count, noise_count, assigned_since_refit,
                    not_assigned_since_refit, not_projected_since_refit, last_refit_event_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    event_id,
                    str(event["rated_at"]),
                    int(event["movie_id"]),
                    1 if refit is not None else 0,
                    refit_reason,
                    len(cluster_counts),
                    noise_count,
                    assigned_since_refit,
                    not_assigned_since_refit,
                    not_projected_since_refit,
                    context["event_id"],
                ),
            )
            target.execute(
                """
                INSERT OR REPLACE INTO visualization_states(user_id, event_id, points_data)
                VALUES (?, ?, ?)
                """,
                (user_id, event_id, _json(points)),
            )
            point_rows += len(points)

            for interest_id, size in sorted(cluster_counts.items()):
                base_size = int(base_cluster_counts.get(interest_id, 0))
                assigned_size = int(assigned_cluster_counts.get(interest_id, 0))
                target.execute(
                    """
                    INSERT OR REPLACE INTO cluster_snapshots(
                        user_id, event_id, cluster_id, size, base_size, assigned_size, total_size, top_genres
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        event_id,
                        interest_id,
                        int(size),
                        base_size,
                        assigned_size,
                        int(size),
                        _json(top_genres_by_interest.get(interest_id, [])),
                    ),
                )
                cluster_rows += 1

            rec_rows = _latest_recommendation_rows(
                source,
                run_id=resolved_run_id,
                user_id=user_id,
                replay_order=replay_order,
            )
            for row in rec_rows:
                target.execute(
                    """
                    INSERT OR REPLACE INTO recommendations(user_id, event_id, rank, movie_id, score, src_cluster)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        event_id,
                        int(row["rank"]),
                        int(row["movie_id"]),
                        None if row["score"] is None else float(row["score"]),
                        None if row["src_cluster"] is None else int(row["src_cluster"]),
                    ),
                )
                recommendation_rows += 1

    return {
        "runId": resolved_run_id,
        "historyDb": str(history_db),
        "dashboardCompactDb": str(output_db),
        "eventTimelineRows": len(events),
        "visualizationPointRows": point_rows,
        "clusterSnapshotRows": cluster_rows,
        "recommendationRows": recommendation_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project POST replay history into compact dashboard SQLite.")
    parser.add_argument("--history-db", type=Path, required=True)
    parser.add_argument("--output-db", type=Path, default=None)
    parser.add_argument("--run-id", type=str, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_db = args.output_db
    if output_db is None:
        output_db = args.history_db.parent.parent / "dashboard_compact" / "dashboard_compact.sqlite"
    summary = compact_history_to_dashboard(history_db=args.history_db, output_db=output_db, run_id=args.run_id)
    print(_json(summary))


if __name__ == "__main__":
    main()
