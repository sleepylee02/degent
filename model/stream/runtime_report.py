from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path("outputs/stream/replay_demo/replay.sqlite")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize a trace replay SQLite runtime store for bottleneck/debug analysis."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to replay.sqlite.")
    parser.add_argument("--run-id", type=str, default=None, help="Run id to report. Defaults to latest run in DB.")
    parser.add_argument("--top-events", type=int, default=10, help="Number of slowest events to include.")
    parser.add_argument("--top-users", type=int, default=10, help="Number of most-active users to include.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--output", type=Path, default=None, help="Optional output path for the report.")
    return parser.parse_args()


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Runtime DB does not exist: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def latest_run_id(conn: sqlite3.Connection) -> str:
    run_id = scalar(
        conn,
        """
        SELECT run_id
        FROM runs
        ORDER BY COALESCE(started_at, created_at, updated_at) DESC
        LIMIT 1
        """,
    )
    if run_id is None:
        raise ValueError("No runs found in runtime DB.")
    return str(run_id)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * pct
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def numeric_summary(values: list[float]) -> dict[str, Any]:
    finite = [float(value) for value in values if value is not None]
    if not finite:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "max": None, "total": 0.0}
    total = sum(finite)
    return {
        "count": len(finite),
        "mean": total / len(finite),
        "p50": percentile(finite, 0.50),
        "p95": percentile(finite, 0.95),
        "max": max(finite),
        "total": total,
    }


def round_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    return value


def rounded_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: round_value(value) for key, value in summary.items()}


def count_by_status(conn: sqlite3.Connection, table: str, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS count FROM {table} WHERE run_id=? GROUP BY status ORDER BY status",
        (run_id,),
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def table_count(conn: sqlite3.Connection, table: str, run_id: str | None = None) -> int:
    if run_id is None:
        return int(scalar(conn, f"SELECT COUNT(*) FROM {table}") or 0)
    return int(scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)) or 0)


def load_stage_report(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT stage, status, latency_sec, attempt_no, error_type, error_message
        FROM stage_attempts
        WHERE run_id=?
        ORDER BY attempt_id
        """,
        (run_id,),
    ).fetchall()
    by_stage: dict[str, dict[str, Any]] = {}
    latencies: dict[str, list[float]] = defaultdict(list)
    statuses: dict[str, Counter[str]] = defaultdict(Counter)
    errors: list[dict[str, Any]] = []
    for row in rows:
        stage = str(row["stage"])
        status = str(row["status"])
        statuses[stage][status] += 1
        if row["latency_sec"] is not None:
            latencies[stage].append(float(row["latency_sec"]))
        if status == "failed":
            errors.append(
                {
                    "stage": stage,
                    "attemptNo": int(row["attempt_no"]),
                    "errorType": row["error_type"],
                    "errorMessage": row["error_message"],
                }
            )
    for stage in sorted(statuses):
        summary = rounded_summary(numeric_summary(latencies[stage]))
        by_stage[stage] = {
            "statusCounts": dict(statuses[stage]),
            **summary,
        }
    dominant = None
    if by_stage:
        dominant = max(by_stage.items(), key=lambda item: item[1].get("total") or 0.0)[0]
    return {"byStage": by_stage, "dominantByTotalLatency": dominant, "errors": errors}


def load_event_report(conn: sqlite3.Connection, run_id: str, top_events: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            ep.event_id,
            ep.status,
            ep.injector_lag_sec,
            ep.processing_lag_sec,
            ep.end_to_end_lag_sec,
            ep.behind_schedule,
            ep.failed_attempts,
            ie.replay_order,
            ie.user_id,
            ie.movie_id,
            ie.rated_at
        FROM event_progress ep
        LEFT JOIN input_events ie
            ON ie.run_id = ep.run_id AND ie.event_id = ep.event_id
        WHERE ep.run_id=?
        ORDER BY COALESCE(ie.replay_order, ep.event_id)
        """,
        (run_id,),
    ).fetchall()
    status_counts = Counter(str(row["status"]) for row in rows)
    injector_lags = [float(row["injector_lag_sec"]) for row in rows if row["injector_lag_sec"] is not None]
    processing_lags = [float(row["processing_lag_sec"]) for row in rows if row["processing_lag_sec"] is not None]
    end_to_end_lags = [float(row["end_to_end_lag_sec"]) for row in rows if row["end_to_end_lag_sec"] is not None]
    slowest = sorted(
        rows,
        key=lambda row: -1.0 if row["end_to_end_lag_sec"] is None else float(row["end_to_end_lag_sec"]),
        reverse=True,
    )[: max(0, top_events)]
    return {
        "statusCounts": dict(status_counts),
        "behindScheduleEvents": sum(1 for row in rows if int(row["behind_schedule"] or 0) == 1),
        "failedAttempts": sum(int(row["failed_attempts"] or 0) for row in rows),
        "injectorLagSec": rounded_summary(numeric_summary(injector_lags)),
        "processingLagSec": rounded_summary(numeric_summary(processing_lags)),
        "endToEndLagSec": rounded_summary(numeric_summary(end_to_end_lags)),
        "slowestEvents": [
            {
                "eventId": row["event_id"],
                "replayOrder": row["replay_order"],
                "userId": row["user_id"],
                "movieId": row["movie_id"],
                "ratedAt": row["rated_at"],
                "status": row["status"],
                "endToEndLagSec": round_value(row["end_to_end_lag_sec"]),
                "processingLagSec": round_value(row["processing_lag_sec"]),
                "injectorLagSec": round_value(row["injector_lag_sec"]),
                "behindSchedule": bool(row["behind_schedule"]),
            }
            for row in slowest
        ],
    }


def load_runtime_metrics_report(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT queue_depth, refit_backlog, open_refit_count, running_refit_count, throughput_events_per_sec
        FROM runtime_metrics
        WHERE run_id=?
        ORDER BY metric_id
        """,
        (run_id,),
    ).fetchall()
    if not rows:
        return {
            "count": 0,
            "maxQueueDepth": None,
            "maxRefitBacklog": None,
            "maxOpenRefitCount": None,
            "lastThroughputEventsPerSec": None,
        }
    return {
        "count": len(rows),
        "maxQueueDepth": max((row["queue_depth"] or 0) for row in rows),
        "maxRefitBacklog": max((row["refit_backlog"] or 0) for row in rows),
        "maxOpenRefitCount": max((row["open_refit_count"] or 0) for row in rows),
        "maxRunningRefitCount": max((row["running_refit_count"] or 0) for row in rows),
        "lastThroughputEventsPerSec": round_value(rows[-1]["throughput_events_per_sec"]),
    }


def load_assignment_report(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT status, already_processed, COUNT(*) AS count
        FROM assignments
        WHERE run_id=?
        GROUP BY status, already_processed
        ORDER BY status, already_processed
        """,
        (run_id,),
    ).fetchall()
    status_counts: Counter[str] = Counter()
    already_processed = 0
    for row in rows:
        count = int(row["count"])
        status_counts[str(row["status"])] += count
        if int(row["already_processed"] or 0) == 1:
            already_processed += count
    return {
        "total": sum(status_counts.values()),
        "statusCounts": dict(status_counts),
        "alreadyProcessed": already_processed,
    }


def load_refit_report(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    requests = rows_to_dicts(
        conn.execute(
            """
            SELECT
                request_id AS requestId,
                user_id AS userId,
                status,
                opened_at AS openedAt,
                running_at AS runningAt,
                closed_at AS closedAt,
                pending_count AS pendingCount,
                assigned_since_last_refit AS assignedSinceLastRefit,
                outlier_since_last_refit AS outlierSinceLastRefit,
                attempt_count AS attemptCount,
                error_type AS errorType,
                error_message AS errorMessage
            FROM refit_requests
            WHERE run_id=?
            ORDER BY opened_at, request_id
            """,
            (run_id,),
        ).fetchall()
    )
    attempts = rows_to_dicts(
        conn.execute(
            """
            SELECT
                request_id AS requestId,
                user_id AS userId,
                status,
                latency_sec AS latencySec,
                active_embedding_rows AS activeEmbeddingRows,
                interest_count AS interestCount,
                backend,
                skip_reason AS skipReason,
                error_type AS errorType,
                error_message AS errorMessage
            FROM refit_attempts
            WHERE run_id=?
            ORDER BY attempt_id
            """,
            (run_id,),
        ).fetchall()
    )
    return {
        "requestStatusCounts": count_by_status(conn, "refit_requests", run_id),
        "attemptStatusCounts": count_by_status(conn, "refit_attempts", run_id),
        "requests": requests,
        "attempts": attempts,
    }


def load_user_report(conn: sqlite3.Connection, run_id: str, top_users: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            user_id AS userId,
            raw_event_count AS rawEventCount,
            positive_event_count AS positiveEventCount,
            active_event_count AS activeEventCount,
            skipped_unknown_items AS skippedUnknownItems,
            last_raw_event_id AS lastRawEventId,
            state_path AS statePath,
            updated_at AS updatedAt
        FROM user_states
        WHERE run_id=?
        ORDER BY raw_event_count DESC, user_id
        """,
        (run_id,),
    ).fetchall()
    top = rows_to_dicts(rows[: max(0, top_users)])
    return {
        "users": len(rows),
        "rawEvents": sum(int(row["rawEventCount"] or 0) for row in rows),
        "positiveEvents": sum(int(row["positiveEventCount"] or 0) for row in rows),
        "activeEvents": sum(int(row["activeEventCount"] or 0) for row in rows),
        "skippedUnknownItems": sum(int(row["skippedUnknownItems"] or 0) for row in rows),
        "topUsersByRawEvents": top,
    }


def load_embedding_report(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    snapshots = conn.execute(
        """
        SELECT kind, store_mode, COUNT(*) AS snapshots, SUM(row_count) AS rows, MAX(row_count) AS maxRows, MAX(dim) AS dim
        FROM embedding_snapshots
        WHERE run_id=?
        GROUP BY kind, store_mode
        ORDER BY kind, store_mode
        """,
        (run_id,),
    ).fetchall()
    repeated_rows = int(
        scalar(conn, "SELECT COUNT(*) FROM embedding_rows WHERE run_id=? AND already_processed=1", (run_id,)) or 0
    )
    return {
        "snapshotGroups": rows_to_dicts(snapshots),
        "rows": table_count(conn, "embedding_rows", run_id),
        "alreadySeenRows": repeated_rows,
    }


def load_recommendation_report(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    return {
        "runs": table_count(conn, "recommendation_runs", run_id),
        "rows": table_count(conn, "recommendation_rows", run_id),
    }


def build_findings(report: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    events = report["events"]
    stages = report["stages"]
    runtime = report["runtimeMetrics"]
    refit = report["refit"]
    assignments = report["assignments"]
    embeddings = report["embeddings"]

    dominant_stage = stages.get("dominantByTotalLatency")
    if dominant_stage:
        stage_summary = stages["byStage"][dominant_stage]
        findings.append(
            f"Dominant stage by total latency: {dominant_stage} "
            f"(total={stage_summary['total']}s, p95={stage_summary['p95']}s)."
        )

    behind = int(events.get("behindScheduleEvents") or 0)
    if behind:
        findings.append(f"{behind} events were behind schedule; inspect slowestEvents and stage p95 latency.")

    max_backlog = runtime.get("maxRefitBacklog")
    if max_backlog:
        findings.append(f"Max refit backlog observed: {max_backlog}.")

    terminal_issues = {
        key: count
        for key, count in refit.get("requestStatusCounts", {}).items()
        if key in {"failed", "skipped", "superseded"} and count
    }
    if terminal_issues:
        findings.append(f"Refit terminal non-closed statuses observed: {terminal_issues}.")

    already_processed = int(assignments.get("alreadyProcessed") or 0)
    already_seen_rows = int(embeddings.get("alreadySeenRows") or 0)
    if already_processed or already_seen_rows:
        findings.append(
            f"Repeated processing signals: assignments.alreadyProcessed={already_processed}, "
            f"embeddingRows.alreadySeenRows={already_seen_rows}."
        )

    if not findings:
        findings.append("No obvious runtime bottleneck signal found in this DB snapshot.")
    return findings


def build_report(db_path: Path, run_id: str | None, *, top_events: int, top_users: int) -> dict[str, Any]:
    with connect(db_path) as conn:
        selected_run_id = run_id or latest_run_id(conn)
        run = conn.execute("SELECT * FROM runs WHERE run_id=?", (selected_run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run id not found in runtime DB: {selected_run_id}")
        report = {
            "dbPath": str(db_path),
            "run": dict(run),
            "tableCounts": {
                table: table_count(conn, table, selected_run_id)
                for table in [
                    "input_events",
                    "event_progress",
                    "stage_attempts",
                    "runtime_metrics",
                    "user_states",
                    "user_raw_events",
                    "user_positive_events",
                    "interest_states",
                    "interest_vectors",
                    "assignments",
                    "refit_requests",
                    "refit_attempts",
                    "embedding_snapshots",
                    "embedding_rows",
                    "recommendation_runs",
                    "recommendation_rows",
                ]
            },
            "events": load_event_report(conn, selected_run_id, top_events),
            "stages": load_stage_report(conn, selected_run_id),
            "runtimeMetrics": load_runtime_metrics_report(conn, selected_run_id),
            "users": load_user_report(conn, selected_run_id, top_users),
            "assignments": load_assignment_report(conn, selected_run_id),
            "refit": load_refit_report(conn, selected_run_id),
            "embeddings": load_embedding_report(conn, selected_run_id),
            "recommendations": load_recommendation_report(conn, selected_run_id),
        }
    report["findings"] = build_findings(report)
    return report


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join("" if value is None else str(value) for value in row) + " |")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    run = report["run"]
    lines: list[str] = [
        f"# Runtime Report: {run['run_id']}",
        "",
        f"- DB: `{report['dbPath']}`",
        f"- Status: `{run['status']}`",
        f"- Started: `{run.get('started_at')}`",
        f"- Ended: `{run.get('ended_at')}`",
        f"- Speed: `{run.get('speed')}`",
        "",
        "## Findings",
        "",
    ]
    lines.extend(f"- {finding}" for finding in report["findings"])
    lines.extend(["", "## Event Lag", ""])
    events = report["events"]
    lines.extend(
        markdown_table(
            ["metric", "count", "mean", "p50", "p95", "max"],
            [
                ["injectorLagSec", *[events["injectorLagSec"].get(key) for key in ["count", "mean", "p50", "p95", "max"]]],
                [
                    "processingLagSec",
                    *[events["processingLagSec"].get(key) for key in ["count", "mean", "p50", "p95", "max"]],
                ],
                [
                    "endToEndLagSec",
                    *[events["endToEndLagSec"].get(key) for key in ["count", "mean", "p50", "p95", "max"]],
                ],
            ],
        )
    )
    lines.extend(["", f"- Status counts: `{events['statusCounts']}`"])
    lines.append(f"- Behind schedule events: `{events['behindScheduleEvents']}`")
    lines.extend(["", "## Stage Latency", ""])
    stage_rows = []
    for stage, summary in report["stages"]["byStage"].items():
        stage_rows.append(
            [
                stage,
                summary.get("statusCounts"),
                summary.get("count"),
                summary.get("mean"),
                summary.get("p50"),
                summary.get("p95"),
                summary.get("max"),
                summary.get("total"),
            ]
        )
    lines.extend(markdown_table(["stage", "status", "count", "mean", "p50", "p95", "max", "total"], stage_rows))
    lines.extend(["", "## Runtime State", ""])
    user_summary = {key: value for key, value in report["users"].items() if key != "topUsersByRawEvents"}
    lines.append(f"- Runtime metrics: `{report['runtimeMetrics']}`")
    lines.append(f"- Users: `{user_summary}`")
    lines.append(f"- Assignments: `{report['assignments']}`")
    lines.append(f"- Refit request statuses: `{report['refit']['requestStatusCounts']}`")
    lines.append(f"- Refit attempt statuses: `{report['refit']['attemptStatusCounts']}`")
    lines.append(f"- Embeddings: `{report['embeddings']}`")
    lines.append(f"- Recommendations: `{report['recommendations']}`")
    lines.extend(["", "## Slowest Events", ""])
    slow_rows = [
        [
            event.get("eventId"),
            event.get("replayOrder"),
            event.get("userId"),
            event.get("movieId"),
            event.get("endToEndLagSec"),
            event.get("processingLagSec"),
            event.get("injectorLagSec"),
            event.get("behindSchedule"),
        ]
        for event in report["events"]["slowestEvents"]
    ]
    lines.extend(
        markdown_table(
            ["eventId", "order", "userId", "movieId", "endToEnd", "processing", "injector", "behind"],
            slow_rows,
        )
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    report = build_report(args.db, args.run_id, top_events=args.top_events, top_users=args.top_users)
    if args.format == "json":
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        rendered = render_markdown(report)

    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
