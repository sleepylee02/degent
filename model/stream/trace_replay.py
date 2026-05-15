from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import argparse
import json
import shutil
import subprocess
import sys
import time

import numpy as np

import model.stream.runtime_store as runtime_store
from model.common.runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    local_timestamp,
    make_run_id,
    sanitize_run_id,
    setup_run_logging,
    update_experiment_manifest,
)


TRACE_REPLAY_SUMMARY_VERSION = "stream_trace_replay_summary.v1"
TRACE_REPLAY_PROGRESS_VERSION = "stream_trace_replay_progress.v1"
INGRESS_EVENT_VERSION = "stream_ingress_event.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay timestamp-sorted rating events with an N-speed trace clock."
    )
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/stream/replay_demo"))
    parser.add_argument("--input-events", type=Path, default=None)
    parser.add_argument(
        "--runtime-db",
        type=Path,
        default=None,
        help="SQLite runtime/state store path. Defaults to <output-root>/replay.sqlite.",
    )
    parser.add_argument("--reset-output", action="store_true", help="Remove output-root before the trace replay run.")
    parser.add_argument("--max-events", type=int, default=None)
    parser.add_argument(
        "--speed",
        type=float,
        default=None,
        help="Trace replay speed multiplier. Example: 100 means 100 trace seconds per 1 wall-clock second.",
    )
    parser.add_argument(
        "--replay-speed",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )

    parser.add_argument("--generate-events", action="store_true", help="Run the C++ replay event generator first.")
    parser.add_argument("--replay-binary", type=Path, default=Path("replay/bin/rating_replay"))
    parser.add_argument("--ratings", type=Path, default=Path("data/ratings_drop_processed.jsonl"))
    parser.add_argument("--replay-user-id", type=int, default=None)
    parser.add_argument("--limit-users", type=int, default=None)
    parser.add_argument("--limit-events", type=int, default=None)
    parser.add_argument("--start-rated-at", type=str, default=None)
    parser.add_argument("--end-rated-at", type=str, default=None)
    parser.add_argument(
        "--seed-user-state-dir",
        type=Path,
        default=None,
        help="Optional pre-T user state dir copied into the replay output root before processing.",
    )
    parser.add_argument(
        "--seed-interest-state-dir",
        type=Path,
        default=None,
        help="Optional pre-T interest state dir copied into the replay output root before processing.",
    )

    parser.add_argument("--movies", type=Path, default=Path("data/movies_processed_drop.csv"))
    parser.add_argument("--checkpoint", type=Path, default=Path("outputs/sasrec_cl.pt"))
    parser.add_argument("--item2idx", type=Path, default=Path("outputs/item2idx.json"))
    parser.add_argument("--min-ratings-for-zscore", type=int, default=3)
    parser.add_argument("--z-threshold", type=float, default=0.0)
    parser.add_argument("--online-batch-size", type=int, default=256)

    parser.add_argument("--similarity-threshold", type=float, default=0.2)
    parser.add_argument("--refit-min-events", type=int, default=20)
    parser.add_argument("--assign-trigger-count", type=int, default=50)
    parser.add_argument("--outlier-trigger-count", type=int, default=10)
    parser.add_argument("--cluster-backend", choices=["auto", "gpu", "cpu"], default="auto")
    parser.add_argument("--min-cluster-size", type=int, default=10)
    parser.add_argument("--cluster-dim", type=int, default=10)
    parser.add_argument("--skip-refit", action="store_true")
    parser.add_argument("--recommend", action="store_true", help="Run online recommend after each emitted event.")
    parser.add_argument("--recommend-top-k", type=int, default=20)
    parser.add_argument("--recommend-normalize", action="store_true")
    return parser.parse_args()


def resolve_speed(args: argparse.Namespace) -> float:
    if args.speed is not None and args.replay_speed is not None and float(args.speed) != float(args.replay_speed):
        raise ValueError("--speed and deprecated --replay-speed disagree. Use --speed.")
    speed = args.speed if args.speed is not None else args.replay_speed
    if speed is None:
        speed = 1.0
    speed = float(speed)
    if speed <= 0:
        raise ValueError("--speed must be positive for trace-clock replay.")
    return speed


def resolve_path(root: Path, path: Path | None) -> Path | None:
    if path is None:
        return None
    return path if path.is_absolute() else root / path


def relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def count_json_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.glob("*.json") if item.is_file())


def directory_metadata(path: Path | None, *, root: Path, copied_to: Path | None = None) -> dict[str, Any] | None:
    if path is None:
        return None
    metadata: dict[str, Any] = {
        "path": relative_or_absolute(root, path),
        "exists": path.exists(),
        "jsonFiles": count_json_files(path),
    }
    if copied_to is not None:
        metadata["copiedTo"] = relative_or_absolute(root, copied_to)
    return metadata


def copy_seed_directory(source: Path | None, destination: Path) -> dict[str, Any] | None:
    if source is None:
        return None
    if not source.exists():
        raise FileNotFoundError(f"Seed directory not found: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Seed path is not a directory: {source}")
    if source.resolve() == destination.resolve():
        return {
            "source": str(source),
            "destination": str(destination),
            "copied": False,
            "jsonFiles": count_json_files(destination),
            "reason": "source_is_destination",
        }

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return {
        "source": str(source),
        "destination": str(destination),
        "copied": True,
        "jsonFiles": count_json_files(destination),
    }


def read_jsonl_slice(path: Path, start: int) -> list[dict[str, Any]]:
    return read_jsonl(path)[start:]


def run_command(
    cmd: list[str],
    *,
    root: Path,
    logger: Any,
    runtime_db: Path | None = None,
    run_id: str | None = None,
    event_id: int | None = None,
    stage: str | None = None,
) -> None:
    logger.info("Running: %s", " ".join(cmd))
    attempt_id = None
    if runtime_db is not None and run_id is not None and stage is not None:
        attempt_id = runtime_store.start_stage_attempt(
            runtime_db,
            run_id=run_id,
            event_id=event_id,
            stage=stage,
            command=cmd,
        )
    try:
        subprocess.run(cmd, cwd=root, check=True)
    except Exception as exc:
        if runtime_db is not None and attempt_id is not None:
            runtime_store.finish_stage_attempt(
                runtime_db,
                attempt_id=attempt_id,
                status="failed",
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
        raise
    if runtime_db is not None and attempt_id is not None:
        runtime_store.finish_stage_attempt(runtime_db, attempt_id=attempt_id, status="completed")


def generated_paths(output_root: Path) -> dict[str, Path]:
    return {
        "replay_input_events": output_root / "replay_input_events.jsonl",
        "ingress_events": output_root / "ingress_events.jsonl",
        "replay_events": output_root / "replay_events.jsonl",
        "replay_summary": output_root / "replay_summary.json",
        "replay_db": output_root / "replay.sqlite",
        "user_state_dir": output_root / "user_states",
        "interest_state_dir": output_root / "interest_states",
        "online_embeddings": output_root / "online_embeddings.npz",
        "online_embedding_events": output_root / "online_embedding_events.jsonl",
        "interest_assignments": output_root / "interest_assignments.jsonl",
        "refit_requests": output_root / "refit_requests.jsonl",
        "refit_events": output_root / "refit_events.jsonl",
        "stream_recommendations": output_root / "stream_recommendations.jsonl",
    }


def run_generator(args: argparse.Namespace, *, root: Path, input_events_path: Path, logger: Any) -> None:
    cmd = [
        str(resolve_path(root, args.replay_binary)),
        "--input",
        str(resolve_path(root, args.ratings)),
        "--output",
        str(input_events_path),
    ]
    if args.replay_user_id is not None:
        cmd.extend(["--user-id", str(args.replay_user_id)])
    if args.limit_users is not None:
        cmd.extend(["--limit-users", str(args.limit_users)])
    if args.limit_events is not None:
        cmd.extend(["--limit-events", str(args.limit_events)])
    if args.start_rated_at is not None:
        cmd.extend(["--start-rated-at", args.start_rated_at])
    if args.end_rated_at is not None:
        cmd.extend(["--end-rated-at", args.end_rated_at])
    run_command(cmd, root=root, logger=logger)


def load_replay_events(path: Path, *, max_events: int | None) -> list[dict[str, Any]]:
    events = read_jsonl(path)
    events.sort(key=lambda item: (float(item["ratedAtTs"]), int(item["userId"]), int(item["movieId"]), int(item["eventId"])))
    if max_events is not None:
        events = events[:max_events]
    return events


def format_dt(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def sleep_until(target_monotonic: float) -> float:
    now = time.monotonic()
    delay = target_monotonic - now
    if delay > 0:
        time.sleep(delay)
        return delay
    return 0.0


def npz_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    data = np.load(path)
    if "embeddings" not in data.files:
        return 0
    return int(data["embeddings"].shape[0])


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "userId": int(event["userId"]),
        "movieId": int(event["movieId"]),
        "rating": float(event["rating"]),
        "ratedAt": str(event["ratedAt"]),
    }


def build_summary(
    *,
    run_id: str,
    status: str,
    started_at: str,
    ended_at: str,
    elapsed_sec: float,
    input_events: int,
    processed_events: int,
    unique_users: int,
    speed: float,
    trace_start_ts: float,
    trace_end_ts: float,
    trace_start_rated_at: str,
    trace_end_rated_at: str,
    totals: dict[str, Any],
    paths: dict[str, Path],
    root: Path,
    seed_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    throughput = None if elapsed_sec <= 0 else processed_events / elapsed_sec
    trace_span_sec = max(0.0, trace_end_ts - trace_start_ts)
    scheduled_span_sec = trace_span_sec / speed if speed > 0 else None
    target_events_per_sec = None
    if scheduled_span_sec is not None and scheduled_span_sec > 0:
        target_events_per_sec = input_events / scheduled_span_sec

    return {
        "version": TRACE_REPLAY_SUMMARY_VERSION,
        "runId": run_id,
        "status": status,
        "startedAt": started_at,
        "endedAt": ended_at,
        "elapsedSec": elapsed_sec,
        "inputEvents": input_events,
        "processedEvents": processed_events,
        "uniqueUsers": unique_users,
        "speed": speed,
        "traceStartTs": trace_start_ts,
        "traceEndTs": trace_end_ts,
        "traceStartRatedAt": trace_start_rated_at,
        "traceEndRatedAt": trace_end_rated_at,
        "traceSpanSec": trace_span_sec,
        "scheduledSpanSec": scheduled_span_sec,
        "throughputEventsPerSec": throughput,
        "targetEventsPerSec": target_events_per_sec,
        "refitBackend": totals.get("refitBackend"),
        "totals": totals,
        "seed": seed_summary or {},
        "paths": {
            "replayInputEvents": relative_or_absolute(root, paths["replay_input_events"]),
            "ingressEvents": relative_or_absolute(root, paths["ingress_events"]),
            "replayEvents": relative_or_absolute(root, paths["replay_events"]),
            "replayDb": relative_or_absolute(root, paths["replay_db"]),
            "onlineEmbeddings": relative_or_absolute(root, paths["online_embeddings"]),
            "onlineEmbeddingEvents": relative_or_absolute(root, paths["online_embedding_events"]),
            "interestAssignments": relative_or_absolute(root, paths["interest_assignments"]),
            "refitRequests": relative_or_absolute(root, paths["refit_requests"]),
            "refitEvents": relative_or_absolute(root, paths["refit_events"]),
            "userStateDir": relative_or_absolute(root, paths["user_state_dir"]),
            "interestStateDir": relative_or_absolute(root, paths["interest_state_dir"]),
            "streamRecommendations": relative_or_absolute(root, paths["stream_recommendations"]),
        },
    }


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "max": 0.0}
    return {"mean": float(np.mean(values)), "max": float(np.max(values))}


def main() -> None:
    args = parse_args()
    speed = resolve_speed(args)
    root = Path(__file__).resolve().parents[2]
    outputs_dir = root / "outputs"
    run_id = sanitize_run_id(args.run_id) if args.run_id else make_run_id("trace_replay")
    run_dir = ensure_experiment_run(root, run_id)
    logger, log_path = setup_run_logging("trace_replay", outputs_dir)

    output_root = resolve_path(root, args.output_root)
    assert output_root is not None
    input_events_path = resolve_path(root, args.input_events) if args.input_events else output_root / "replay_input_events.jsonl"

    if args.reset_output:
        if not args.generate_events and input_events_path.resolve().is_relative_to(output_root.resolve()):
            raise ValueError("--reset-output would remove --input-events. Use --generate-events or an external input path.")
        if output_root.exists():
            shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    paths = generated_paths(output_root)
    paths["replay_input_events"] = input_events_path
    runtime_db_path = resolve_path(root, args.runtime_db) if args.runtime_db else paths["replay_db"]
    assert runtime_db_path is not None
    paths["replay_db"] = runtime_db_path
    runtime_store.init_store(runtime_db_path)

    seed_user_state_dir = resolve_path(root, args.seed_user_state_dir) if args.seed_user_state_dir else None
    seed_interest_state_dir = (
        resolve_path(root, args.seed_interest_state_dir) if args.seed_interest_state_dir else None
    )
    seed_summary = {
        "userState": copy_seed_directory(seed_user_state_dir, paths["user_state_dir"]),
        "interestState": copy_seed_directory(seed_interest_state_dir, paths["interest_state_dir"]),
    }

    logger.info("Experiment run id: %s", run_id)
    logger.info("Output root: %s", output_root)
    logger.info("Replay input events: %s", input_events_path)
    logger.info("Runtime DB: %s", runtime_db_path)
    logger.info("Trace replay speed: %.6g", speed)
    logger.info("Seed summary: %s", seed_summary)

    if args.generate_events:
        run_generator(args, root=root, input_events_path=input_events_path, logger=logger)
    if not input_events_path.exists():
        raise FileNotFoundError(f"Replay input events not found: {input_events_path}")

    events = load_replay_events(input_events_path, max_events=args.max_events)
    if not events:
        raise ValueError(f"No replay events to process: {input_events_path}")

    trace_start_ts = float(events[0]["ratedAtTs"])
    trace_end_ts = float(events[-1]["ratedAtTs"])
    trace_start_rated_at = str(events[0]["ratedAt"])
    trace_end_rated_at = str(events[-1]["ratedAt"])
    started_at = local_timestamp()
    start_time = time.time()
    wall_start_mono = time.monotonic()
    wall_start_dt = datetime.now().astimezone()

    totals: dict[str, Any] = {
        "activeEmbeddingRows": 0,
        "assignmentRecords": 0,
        "refitRequestsOpened": 0,
        "refitClosed": 0,
        "refitSkipped": 0,
        "recommendationRows": 0,
        "behindScheduleEvents": 0,
        "refitBackend": args.cluster_backend,
        "maxInjectorLagSec": 0.0,
        "meanInjectorLagSec": 0.0,
        "maxProcessingLagSec": 0.0,
        "meanProcessingLagSec": 0.0,
        "maxEndToEndLagSec": 0.0,
        "meanEndToEndLagSec": 0.0,
    }
    injector_lags: list[float] = []
    processing_lags: list[float] = []
    end_to_end_lags: list[float] = []
    unique_users_seen: set[int] = set()
    processed_events = 0
    current_event_id: int | None = None

    runtime_store.upsert_run(
        runtime_db_path,
        run_id=run_id,
        status="running",
        speed=speed,
        output_root=relative_or_absolute(root, output_root),
        summary_path=relative_or_absolute(root, paths["replay_summary"]),
        started_at=started_at,
    )
    runtime_store.record_artifact(
        runtime_db_path,
        run_id=run_id,
        kind="replay_input_events",
        path=relative_or_absolute(root, input_events_path),
        row_count=len(events),
    )

    append_jsonl(
        paths["replay_events"],
        {
            "version": TRACE_REPLAY_PROGRESS_VERSION,
            "recordedAt": local_timestamp(),
            "runId": run_id,
            "stage": "start",
            "status": "started",
            "speed": speed,
            "traceStartTs": trace_start_ts,
            "traceEndTs": trace_end_ts,
            "traceStartRatedAt": trace_start_rated_at,
            "traceEndRatedAt": trace_end_rated_at,
            "processedEvents": 0,
            "uniqueUsers": 0,
        },
    )

    try:
        for ordinal, event in enumerate(events):
            current_event_id = int(event["eventId"])
            event_trace_ts = float(event["ratedAtTs"])
            scheduled_offset_sec = max(0.0, (event_trace_ts - trace_start_ts) / speed)
            scheduled_monotonic = wall_start_mono + scheduled_offset_sec
            scheduled_at_dt = wall_start_dt + timedelta(seconds=scheduled_offset_sec)
            slept_sec = sleep_until(scheduled_monotonic)
            emitted_mono = time.monotonic()
            emitted_dt = datetime.now().astimezone()
            injector_lag_sec = max(0.0, emitted_mono - scheduled_monotonic)
            behind_schedule = injector_lag_sec > 1e-3

            ingress_record = {
                "version": INGRESS_EVENT_VERSION,
                "recordedAt": format_dt(emitted_dt),
                "runId": run_id,
                "eventId": int(event["eventId"]),
                "replayOrder": int(event.get("replayOrder", ordinal)),
                "userId": int(event["userId"]),
                "movieId": int(event["movieId"]),
                "rating": float(event["rating"]),
                "ratedAt": str(event["ratedAt"]),
                "ratedAtTs": event_trace_ts,
                "source": event.get("source", "ratings_drop_processed"),
                "speed": speed,
                "scheduledAt": format_dt(scheduled_at_dt),
                "emittedAt": format_dt(emitted_dt),
                "injectorLagSec": injector_lag_sec,
                "behindSchedule": behind_schedule,
            }
            append_jsonl(paths["ingress_events"], ingress_record)
            runtime_store.record_input_event(runtime_db_path, run_id=run_id, event=ingress_record)
            runtime_store.record_event_progress(
                runtime_db_path,
                run_id=run_id,
                event_id=current_event_id,
                status="emitted",
                scheduled_at=format_dt(scheduled_at_dt),
                emitted_at=format_dt(emitted_dt),
                injector_lag_sec=injector_lag_sec,
                behind_schedule=behind_schedule,
            )

            event_start = time.time()
            processing_started_dt = datetime.now().astimezone()
            runtime_store.record_event_progress(
                runtime_db_path,
                run_id=run_id,
                event_id=current_event_id,
                status="processing",
                processing_started_at=format_dt(processing_started_dt),
            )
            before_assignment_lines = count_jsonl(paths["interest_assignments"])
            before_refit_request_lines = count_jsonl(paths["refit_requests"])
            before_refit_event_lines = count_jsonl(paths["refit_events"])

            payload = event_payload(event)
            run_command(
                [
                    sys.executable,
                    "-m",
                    "model.stream.extract_online",
                    "--run-id",
                    run_id,
                    "--movies",
                    str(resolve_path(root, args.movies)),
                    "--checkpoint",
                    str(resolve_path(root, args.checkpoint)),
                    "--item2idx",
                    str(resolve_path(root, args.item2idx)),
                    "--state-dir",
                    str(paths["user_state_dir"]),
                    "--output",
                    str(paths["online_embeddings"]),
                    "--event-log",
                    str(paths["online_embedding_events"]),
                    "--event-json",
                    json.dumps(payload, ensure_ascii=False),
                    "--min-ratings-for-zscore",
                    str(args.min_ratings_for_zscore),
                    "--z-threshold",
                    str(args.z_threshold),
                    "--batch-size",
                    str(args.online_batch_size),
                    "--runtime-db",
                    str(runtime_db_path),
                    "--event-id",
                    str(current_event_id),
                ],
                root=root,
                logger=logger,
                runtime_db=runtime_db_path,
                run_id=run_id,
                event_id=current_event_id,
                stage="extract_online",
            )

            active_rows = npz_row_count(paths["online_embeddings"])
            run_command(
                [
                    sys.executable,
                    "-m",
                    "model.stream.interest_assign",
                    "--run-id",
                    run_id,
                    "--embeddings",
                    str(paths["online_embeddings"]),
                    "--interest-state-dir",
                    str(paths["interest_state_dir"]),
                    "--assignments",
                    str(paths["interest_assignments"]),
                    "--refit-requests",
                    str(paths["refit_requests"]),
                    "--similarity-threshold",
                    str(args.similarity_threshold),
                    "--refit-min-events",
                    str(args.refit_min_events),
                    "--assign-trigger-count",
                    str(args.assign_trigger_count),
                    "--outlier-trigger-count",
                    str(args.outlier_trigger_count),
                    "--runtime-db",
                    str(runtime_db_path),
                    "--event-id",
                    str(current_event_id),
                ],
                root=root,
                logger=logger,
                runtime_db=runtime_db_path,
                run_id=run_id,
                event_id=current_event_id,
                stage="interest_assign",
            )

            new_assignment_records = read_jsonl_slice(paths["interest_assignments"], before_assignment_lines)
            new_refit_requests = read_jsonl_slice(paths["refit_requests"], before_refit_request_lines)
            new_request_users = sorted({int(item["userId"]) for item in new_refit_requests if item.get("status") == "open"})

            if not args.skip_refit:
                for user_id in new_request_users:
                    run_command(
                        [
                            sys.executable,
                            "-m",
                            "model.stream.cluster_refit",
                            "--run-id",
                            run_id,
                            "--embeddings",
                            str(paths["online_embeddings"]),
                            "--refit-requests",
                            str(paths["refit_requests"]),
                            "--interest-state-dir",
                            str(paths["interest_state_dir"]),
                            "--refit-events",
                            str(paths["refit_events"]),
                            "--cluster-backend",
                            args.cluster_backend,
                            "--refit-min-events",
                            str(args.refit_min_events),
                            "--min-cluster-size",
                            str(args.min_cluster_size),
                            "--cluster-dim",
                            str(args.cluster_dim),
                            "--movies",
                            str(resolve_path(root, args.movies)),
                            "--user-id",
                            str(user_id),
                            "--runtime-db",
                            str(runtime_db_path),
                            "--event-id",
                            str(current_event_id),
                        ],
                        root=root,
                        logger=logger,
                        runtime_db=runtime_db_path,
                        run_id=run_id,
                        event_id=current_event_id,
                        stage="cluster_refit",
                    )

            before_recommend_lines = count_jsonl(paths["stream_recommendations"])
            if args.recommend:
                recommend_cmd = [
                    sys.executable,
                    "-m",
                    "model.stream.recommend_online",
                    "--run-id",
                    run_id,
                    "--interest-state-dir",
                    str(paths["interest_state_dir"]),
                    "--user-state-dir",
                    str(paths["user_state_dir"]),
                    "--checkpoint",
                    str(resolve_path(root, args.checkpoint)),
                    "--item2idx",
                    str(resolve_path(root, args.item2idx)),
                    "--movies",
                    str(resolve_path(root, args.movies)),
                    "--output-jsonl",
                    str(paths["stream_recommendations"]),
                    "--top-k",
                    str(args.recommend_top_k),
                    "--user-id",
                    str(int(event["userId"])),
                    "--runtime-db",
                    str(runtime_db_path),
                    "--event-id",
                    str(current_event_id),
                ]
                if args.recommend_normalize:
                    recommend_cmd.append("--normalize")
                run_command(
                    recommend_cmd,
                    root=root,
                    logger=logger,
                    runtime_db=runtime_db_path,
                    run_id=run_id,
                    event_id=current_event_id,
                    stage="recommend_online",
                )

            processed_dt = datetime.now().astimezone()
            processed_mono = time.monotonic()
            event_latency_sec = time.time() - event_start
            processing_lag_sec = max(0.0, processed_mono - emitted_mono)
            end_to_end_lag_sec = max(0.0, processed_mono - scheduled_monotonic)
            new_refit_events = read_jsonl_slice(paths["refit_events"], before_refit_event_lines)
            new_recommend_rows = count_jsonl(paths["stream_recommendations"]) - before_recommend_lines
            assignment_status_counts = Counter(str(item.get("status", "unknown")) for item in new_assignment_records)
            refit_closed = sum(1 for item in new_refit_events if item.get("status") == "closed")
            refit_skipped = sum(1 for item in new_refit_events if item.get("status") == "skipped")

            unique_users_seen.add(int(event["userId"]))
            processed_events += 1
            injector_lags.append(injector_lag_sec)
            processing_lags.append(processing_lag_sec)
            end_to_end_lags.append(end_to_end_lag_sec)

            totals["activeEmbeddingRows"] += active_rows
            totals["assignmentRecords"] += len(new_assignment_records)
            totals["refitRequestsOpened"] += len(new_refit_requests)
            totals["refitClosed"] += refit_closed
            totals["refitSkipped"] += refit_skipped
            totals["recommendationRows"] += new_recommend_rows
            totals["behindScheduleEvents"] += int(behind_schedule)

            injector_summary = latency_summary(injector_lags)
            processing_summary = latency_summary(processing_lags)
            e2e_summary = latency_summary(end_to_end_lags)
            totals["meanInjectorLagSec"] = injector_summary["mean"]
            totals["maxInjectorLagSec"] = injector_summary["max"]
            totals["meanProcessingLagSec"] = processing_summary["mean"]
            totals["maxProcessingLagSec"] = processing_summary["max"]
            totals["meanEndToEndLagSec"] = e2e_summary["mean"]
            totals["maxEndToEndLagSec"] = e2e_summary["max"]
            runtime_store.record_event_progress(
                runtime_db_path,
                run_id=run_id,
                event_id=current_event_id,
                status="completed",
                processed_at=format_dt(processed_dt),
                processing_lag_sec=processing_lag_sec,
                end_to_end_lag_sec=end_to_end_lag_sec,
                behind_schedule=behind_schedule,
            )
            runtime_store.record_runtime_metric(
                runtime_db_path,
                run_id=run_id,
                event_id=current_event_id,
                queue_depth=0,
                refit_backlog=totals["refitRequestsOpened"] - totals["refitClosed"] - totals["refitSkipped"],
                throughput_events_per_sec=processed_events / max(time.time() - start_time, 1e-9),
                notes={
                    "activeEmbeddingRows": active_rows,
                    "assignmentStatusCounts": dict(assignment_status_counts),
                    "recommendationRows": new_recommend_rows,
                },
            )

            append_jsonl(
                paths["replay_events"],
                {
                    "version": TRACE_REPLAY_PROGRESS_VERSION,
                    "recordedAt": format_dt(processed_dt),
                    "runId": run_id,
                    "stage": "trace_event",
                    "status": "completed",
                    "eventOrdinal": ordinal,
                    "eventId": int(event["eventId"]),
                    "replayOrder": int(event.get("replayOrder", ordinal)),
                    "userId": int(event["userId"]),
                    "movieId": int(event["movieId"]),
                    "ratedAt": str(event["ratedAt"]),
                    "ratedAtTs": event_trace_ts,
                    "speed": speed,
                    "scheduledAt": format_dt(scheduled_at_dt),
                    "emittedAt": format_dt(emitted_dt),
                    "processingStartedAt": format_dt(processing_started_dt),
                    "processedAt": format_dt(processed_dt),
                    "sleptSec": slept_sec,
                    "injectorLagSec": injector_lag_sec,
                    "processingLagSec": processing_lag_sec,
                    "endToEndLagSec": end_to_end_lag_sec,
                    "behindSchedule": behind_schedule,
                    "queueDepth": 0,
                    "processedEvents": processed_events,
                    "uniqueUsers": len(unique_users_seen),
                    "activeEmbeddingRows": active_rows,
                    "assignmentStatusCounts": dict(assignment_status_counts),
                    "refitRequestsOpened": len(new_refit_requests),
                    "refitClosed": refit_closed,
                    "refitSkipped": refit_skipped,
                    "recommendationRows": new_recommend_rows,
                    "latencySec": event_latency_sec,
                },
            )

        ended_at = local_timestamp()
        elapsed_sec = time.time() - start_time
        summary = build_summary(
            run_id=run_id,
            status="completed",
            started_at=started_at,
            ended_at=ended_at,
            elapsed_sec=elapsed_sec,
            input_events=len(events),
            processed_events=processed_events,
            unique_users=len(unique_users_seen),
            speed=speed,
            trace_start_ts=trace_start_ts,
            trace_end_ts=trace_end_ts,
            trace_start_rated_at=trace_start_rated_at,
            trace_end_rated_at=trace_end_rated_at,
            totals=totals,
            paths=paths,
            root=root,
            seed_summary=seed_summary,
        )
        write_json(paths["replay_summary"], summary)
        runtime_store.upsert_run(
            runtime_db_path,
            run_id=run_id,
            status="completed",
            ended_at=ended_at,
            speed=speed,
            output_root=relative_or_absolute(root, output_root),
            summary_path=relative_or_absolute(root, paths["replay_summary"]),
        )
        append_jsonl(
            paths["replay_events"],
            {
                "version": TRACE_REPLAY_PROGRESS_VERSION,
                "recordedAt": ended_at,
                "runId": run_id,
                "stage": "end",
                "status": "completed",
                "speed": speed,
                "processedEvents": processed_events,
                "uniqueUsers": len(unique_users_seen),
                "latencySec": elapsed_sec,
            },
        )

        append_metric(run_dir, {"stage": "trace_replay", **summary})
        update_experiment_manifest(
            run_dir,
            {
                "run_id": run_id,
                "git": git_metadata(root),
                "stages": {
                    "trace_replay": {
                        "command": command_line(),
                        "log": file_metadata(log_path, root=root),
                        "inputs": {
                            "replay_input_events": file_metadata(input_events_path, root=root, include_sha256=True),
                            "seed_user_state_dir": directory_metadata(
                                seed_user_state_dir,
                                root=root,
                                copied_to=paths["user_state_dir"],
                            ),
                            "seed_interest_state_dir": directory_metadata(
                                seed_interest_state_dir,
                                root=root,
                                copied_to=paths["interest_state_dir"],
                            ),
                        },
                        "outputs": {
                            "replay_summary": file_metadata(paths["replay_summary"], root=root, include_sha256=True),
                            "replay_events": file_metadata(paths["replay_events"], root=root, include_sha256=True),
                            "ingress_events": file_metadata(paths["ingress_events"], root=root, include_sha256=True),
                            "replay_db": file_metadata(paths["replay_db"], root=root, include_sha256=True),
                        },
                        "summary_metrics": summary,
                    }
                },
            },
        )
        logger.info("Trace replay completed: %s", summary)
    except Exception as exc:
        ended_at = local_timestamp()
        elapsed_sec = time.time() - start_time
        append_jsonl(
            paths["replay_events"],
            {
                "version": TRACE_REPLAY_PROGRESS_VERSION,
                "recordedAt": ended_at,
                "runId": run_id,
                "stage": "error",
                "status": "failed",
                "speed": speed,
                "processedEvents": processed_events,
                "uniqueUsers": len(unique_users_seen),
                "latencySec": elapsed_sec,
                "error": f"{exc.__class__.__name__}: {exc}",
            },
        )
        if current_event_id is not None:
            runtime_store.record_event_progress(
                runtime_db_path,
                run_id=run_id,
                event_id=current_event_id,
                status="failed",
                failed_attempts=1,
            )
        summary = build_summary(
            run_id=run_id,
            status="failed",
            started_at=started_at,
            ended_at=ended_at,
            elapsed_sec=elapsed_sec,
            input_events=len(events),
            processed_events=processed_events,
            unique_users=len(unique_users_seen),
            speed=speed,
            trace_start_ts=trace_start_ts,
            trace_end_ts=trace_end_ts,
            trace_start_rated_at=trace_start_rated_at,
            trace_end_rated_at=trace_end_rated_at,
            totals=totals,
            paths=paths,
            root=root,
            seed_summary=seed_summary,
        )
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
        write_json(paths["replay_summary"], summary)
        runtime_store.upsert_run(
            runtime_db_path,
            run_id=run_id,
            status="failed",
            ended_at=ended_at,
            speed=speed,
            output_root=relative_or_absolute(root, output_root),
            summary_path=relative_or_absolute(root, paths["replay_summary"]),
        )
        raise


if __name__ == "__main__":
    main()
