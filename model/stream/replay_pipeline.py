from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import argparse
import json
import shutil
import subprocess
import sys
import time

import numpy as np

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


REPLAY_SUMMARY_VERSION = "stream_replay_summary.v1"
REPLAY_PROGRESS_VERSION = "stream_replay_progress.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a file-based replay over the streaming online embedding, interest assignment, and refit pipeline."
    )
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/stream/replay_demo"))
    parser.add_argument("--input-events", type=Path, default=None)
    parser.add_argument("--reset-output", action="store_true", help="Remove output-root before the replay run.")
    parser.add_argument("--micro-batch-size", type=int, default=20)
    parser.add_argument("--max-events", type=int, default=None)
    parser.add_argument(
        "--replay-speed",
        type=float,
        default=0.0,
        help="Virtual replay speed multiplier. 0 disables wall-clock pacing.",
    )
    parser.add_argument(
        "--max-sleep-sec",
        type=float,
        default=1.0,
        help="Maximum wall-clock sleep before a micro-batch when replay pacing is enabled.",
    )

    parser.add_argument("--generate-events", action="store_true", help="Run the C++ replay event generator first.")
    parser.add_argument("--replay-binary", type=Path, default=Path("replay/bin/rating_replay"))
    parser.add_argument("--ratings", type=Path, default=Path("data/ratings_drop_processed.jsonl"))
    parser.add_argument("--replay-user-id", type=int, default=None)
    parser.add_argument("--limit-users", type=int, default=None)
    parser.add_argument("--limit-events", type=int, default=None)
    parser.add_argument("--start-rated-at", type=str, default=None)
    parser.add_argument("--end-rated-at", type=str, default=None)

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
    return parser.parse_args()


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


def read_jsonl_slice(path: Path, start: int) -> list[dict[str, Any]]:
    return read_jsonl(path)[start:]


def write_batch_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            item = {
                "userId": int(event["userId"]),
                "movieId": int(event["movieId"]),
                "rating": float(event["rating"]),
                "ratedAt": str(event["ratedAt"]),
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def run_command(cmd: list[str], *, root: Path, logger: Any) -> None:
    logger.info("Running: %s", " ".join(cmd))
    subprocess.run(cmd, cwd=root, check=True)


def generated_paths(output_root: Path) -> dict[str, Path]:
    return {
        "replay_input_events": output_root / "replay_input_events.jsonl",
        "replay_events": output_root / "replay_events.jsonl",
        "replay_summary": output_root / "replay_summary.json",
        "user_state_dir": output_root / "user_states",
        "interest_state_dir": output_root / "interest_states",
        "online_embeddings": output_root / "online_embeddings.npz",
        "online_embedding_events": output_root / "online_embedding_events.jsonl",
        "interest_assignments": output_root / "interest_assignments.jsonl",
        "refit_requests": output_root / "refit_requests.jsonl",
        "refit_events": output_root / "refit_events.jsonl",
        "batch_dir": output_root / "batches",
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


def sleep_for_replay_clock(
    *,
    previous_ts: float | None,
    current_ts: float,
    replay_speed: float,
    max_sleep_sec: float,
) -> float:
    if previous_ts is None or replay_speed <= 0:
        return 0.0
    delay_sec = max(0.0, (current_ts - previous_ts) / replay_speed)
    delay_sec = min(delay_sec, max(0.0, max_sleep_sec))
    if delay_sec > 0:
        time.sleep(delay_sec)
    return delay_sec


def npz_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    data = np.load(path)
    if "embeddings" not in data.files:
        return 0
    return int(data["embeddings"].shape[0])


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
    micro_batch_size: int,
    refit_backend: str,
    totals: dict[str, Any],
    paths: dict[str, Path],
    root: Path,
) -> dict[str, Any]:
    return {
        "version": REPLAY_SUMMARY_VERSION,
        "runId": run_id,
        "status": status,
        "startedAt": started_at,
        "endedAt": ended_at,
        "elapsedSec": elapsed_sec,
        "inputEvents": input_events,
        "processedEvents": processed_events,
        "uniqueUsers": unique_users,
        "microBatchSize": micro_batch_size,
        "refitBackend": refit_backend,
        "totals": totals,
        "paths": {
            "replayEvents": relative_or_absolute(root, paths["replay_events"]),
            "onlineEmbeddings": relative_or_absolute(root, paths["online_embeddings"]),
            "interestAssignments": relative_or_absolute(root, paths["interest_assignments"]),
            "refitRequests": relative_or_absolute(root, paths["refit_requests"]),
            "refitEvents": relative_or_absolute(root, paths["refit_events"]),
            "interestStateDir": relative_or_absolute(root, paths["interest_state_dir"]),
        },
    }


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    outputs_dir = root / "outputs"
    run_id = sanitize_run_id(args.run_id) if args.run_id else make_run_id("phase5_replay")
    run_dir = ensure_experiment_run(root, run_id)
    logger, log_path = setup_run_logging("replay_pipeline", outputs_dir)

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

    logger.info("Experiment run id: %s", run_id)
    logger.info("Output root: %s", output_root)
    logger.info("Replay input events: %s", input_events_path)

    if args.generate_events:
        run_generator(args, root=root, input_events_path=input_events_path, logger=logger)
    if not input_events_path.exists():
        raise FileNotFoundError(f"Replay input events not found: {input_events_path}")

    events = load_replay_events(input_events_path, max_events=args.max_events)
    if not events:
        raise ValueError(f"No replay events to process: {input_events_path}")

    started_at = local_timestamp()
    start_time = time.time()
    totals: dict[str, Any] = {
        "activeEmbeddingRows": 0,
        "assignmentRecords": 0,
        "refitRequestsOpened": 0,
        "refitClosed": 0,
        "refitSkipped": 0,
    }
    unique_users_seen: set[int] = set()
    processed_events = 0
    previous_replay_ts: float | None = None

    append_jsonl(
        paths["replay_events"],
        {
            "version": REPLAY_PROGRESS_VERSION,
            "recordedAt": local_timestamp(),
            "runId": run_id,
            "stage": "start",
            "status": "started",
            "processedEvents": 0,
            "uniqueUsers": 0,
        },
    )

    try:
        for batch_id, start in enumerate(range(0, len(events), args.micro_batch_size)):
            batch = events[start : start + args.micro_batch_size]
            replay_clock_ts = float(batch[-1]["ratedAtTs"])
            replay_sleep_sec = sleep_for_replay_clock(
                previous_ts=previous_replay_ts,
                current_ts=replay_clock_ts,
                replay_speed=args.replay_speed,
                max_sleep_sec=args.max_sleep_sec,
            )
            previous_replay_ts = replay_clock_ts

            batch_start = time.time()
            paths["batch_dir"].mkdir(parents=True, exist_ok=True)
            batch_path = paths["batch_dir"] / f"batch_{batch_id:06d}.jsonl"
            write_batch_events(batch_path, batch)

            before_assignment_lines = count_jsonl(paths["interest_assignments"])
            before_refit_request_lines = count_jsonl(paths["refit_requests"])
            before_refit_event_lines = count_jsonl(paths["refit_events"])

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
                    "--event-jsonl",
                    str(batch_path),
                    "--min-ratings-for-zscore",
                    str(args.min_ratings_for_zscore),
                    "--z-threshold",
                    str(args.z_threshold),
                    "--batch-size",
                    str(args.online_batch_size),
                ],
                root=root,
                logger=logger,
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
                ],
                root=root,
                logger=logger,
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
                            "--user-id",
                            str(user_id),
                        ],
                        root=root,
                        logger=logger,
                    )

            new_refit_events = read_jsonl_slice(paths["refit_events"], before_refit_event_lines)
            assignment_status_counts = Counter(str(item.get("status", "unknown")) for item in new_assignment_records)
            refit_closed = sum(1 for item in new_refit_events if item.get("status") == "closed")
            refit_skipped = sum(1 for item in new_refit_events if item.get("status") == "skipped")
            batch_users = {int(item["userId"]) for item in batch}
            unique_users_seen.update(batch_users)
            processed_events += len(batch)

            totals["activeEmbeddingRows"] += active_rows
            totals["assignmentRecords"] += len(new_assignment_records)
            totals["refitRequestsOpened"] += len(new_refit_requests)
            totals["refitClosed"] += refit_closed
            totals["refitSkipped"] += refit_skipped

            append_jsonl(
                paths["replay_events"],
                {
                    "version": REPLAY_PROGRESS_VERSION,
                    "recordedAt": local_timestamp(),
                    "runId": run_id,
                    "stage": "micro_batch",
                    "status": "completed",
                    "batchId": batch_id,
                    "eventStart": int(batch[0].get("replayOrder", start)),
                    "eventEnd": int(batch[-1].get("replayOrder", start + len(batch) - 1)),
                    "replayClockTs": replay_clock_ts,
                    "replayClockRatedAt": str(batch[-1]["ratedAt"]),
                    "replaySleepSec": replay_sleep_sec,
                    "processedEvents": len(batch),
                    "uniqueUsers": len(batch_users),
                    "activeEmbeddingRows": active_rows,
                    "assignmentStatusCounts": dict(assignment_status_counts),
                    "refitRequestsOpened": len(new_refit_requests),
                    "refitClosed": refit_closed,
                    "refitSkipped": refit_skipped,
                    "latencySec": time.time() - batch_start,
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
            micro_batch_size=args.micro_batch_size,
            refit_backend=args.cluster_backend,
            totals=totals,
            paths=paths,
            root=root,
        )
        write_json(paths["replay_summary"], summary)
        append_jsonl(
            paths["replay_events"],
            {
                "version": REPLAY_PROGRESS_VERSION,
                "recordedAt": ended_at,
                "runId": run_id,
                "stage": "end",
                "status": "completed",
                "processedEvents": processed_events,
                "uniqueUsers": len(unique_users_seen),
                "latencySec": elapsed_sec,
            },
        )

        append_metric(run_dir, {"stage": "replay_pipeline", **summary})
        update_experiment_manifest(
            run_dir,
            {
                "run_id": run_id,
                "git": git_metadata(root),
                "stages": {
                    "replay_pipeline": {
                        "command": command_line(),
                        "log": file_metadata(log_path, root=root),
                        "inputs": {
                            "replay_input_events": file_metadata(input_events_path, root=root, include_sha256=True),
                        },
                        "outputs": {
                            "replay_summary": file_metadata(paths["replay_summary"], root=root, include_sha256=True),
                            "replay_events": file_metadata(paths["replay_events"], root=root, include_sha256=True),
                        },
                        "summary_metrics": summary,
                    }
                },
            },
        )
        logger.info("Replay completed: %s", summary)
    except Exception as exc:
        ended_at = local_timestamp()
        elapsed_sec = time.time() - start_time
        append_jsonl(
            paths["replay_events"],
            {
                "version": REPLAY_PROGRESS_VERSION,
                "recordedAt": ended_at,
                "runId": run_id,
                "stage": "error",
                "status": "failed",
                "processedEvents": processed_events,
                "uniqueUsers": len(unique_users_seen),
                "latencySec": elapsed_sec,
                "error": f"{exc.__class__.__name__}: {exc}",
            },
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
            micro_batch_size=args.micro_batch_size,
            refit_backend=args.cluster_backend,
            totals=totals,
            paths=paths,
            root=root,
        )
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
        write_json(paths["replay_summary"], summary)
        raise


if __name__ == "__main__":
    main()
