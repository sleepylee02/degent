from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import shutil

from tqdm import tqdm

import model.stream.runtime_store as runtime_store
from model.common.dataset import parse_ts
from model.common.runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    load_experiment_manifest,
    local_timestamp,
    resolve_model_run_id,
    schema_metadata,
    setup_run_logging,
    update_experiment_manifest,
)
from model.stream.interest_assign import (
    load_interest_state,
    save_interest_state,
    state_path_for_user as interest_path_for_user,
)
from model.stream.state import (
    PositivePolicy,
    active_positive_events,
    build_state_from_rating_history,
    save_user_state,
    state_path_for_user,
)


SEED_SUMMARY_VERSION = "pre_t_user_state_seed.v1"
SEED_USER_EVENT_ROWS_STORED = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build replay-ready user state from ratings strictly before a temporal cutoff."
    )
    parser.add_argument("--run-id", type=str, default=None, help="Experiment run id. Defaults to latest train run.")
    parser.add_argument("--hash-inputs", action="store_true", help="Compute SHA256 for input data files.")
    parser.add_argument(
        "--hash-limit-mb",
        type=int,
        default=100,
        help="Max file size for SHA256 hashing. Use -1 for no limit.",
    )
    parser.add_argument("--ratings", type=Path, default=Path("data/ratings_drop_processed.jsonl"))
    parser.add_argument("--item2idx", type=Path, default=Path("outputs/item2idx.json"))
    parser.add_argument("--state-db", type=Path, default=Path("outputs/pre/temporal_2022/state.sqlite"))
    parser.add_argument(
        "--interest-state-db",
        type=Path,
        default=None,
        help="SQLite interest state store to mark pre-T active rawEventIds as processed. Defaults to --state-db.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Optional legacy per-user JSON user state output directory.",
    )
    parser.add_argument(
        "--interest-state-dir",
        type=Path,
        default=None,
        help="Optional existing interest state dir to mark pre-T active rawEventIds as processed.",
    )
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument(
        "--max-rated-at-exclusive",
        type=str,
        required=True,
        help="Seed with ratings whose ratedAt is strictly before this UTC ISO timestamp.",
    )
    parser.add_argument("--limit-users", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--seq-len", type=int, default=None)
    parser.add_argument("--min-ratings-for-zscore", type=int, default=3)
    parser.add_argument("--z-threshold", type=float, default=0.0)
    parser.add_argument(
        "--reset-state-dir",
        action="store_true",
        help="Remove legacy state-dir before writing seeded JSON states.",
    )
    parser.add_argument(
        "--reset-state-db",
        action="store_true",
        help="Remove state-db before writing seeded SQLite states.",
    )
    return parser.parse_args()


def resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_item2idx(path: Path) -> dict[int, int]:
    with path.open("r", encoding="utf-8") as handle:
        return {int(k): int(v) for k, v in json.load(handle).items()}


def cutoff_filter(ratings: list[dict[str, Any]], cutoff_ts: float) -> list[dict[str, Any]]:
    return [rating for rating in ratings if parse_ts(str(rating["ratedAt"])) < cutoff_ts]


def write_json(path: Path, record: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def maybe_mark_interest_processed(
    *,
    interest_state_dir: Path | None,
    interest_state_db: Path | None,
    interest_state_conn: Any | None,
    run_id: str,
    user_id: int,
    processed_raw_event_ids: list[int],
) -> bool:
    path = None if interest_state_dir is None else interest_path_for_user(interest_state_dir, user_id)
    state = None
    if interest_state_conn is not None:
        payload = runtime_store.fetch_interest_state_payload_conn(interest_state_conn, run_id=run_id, user_id=user_id)
        if payload is not None:
            from model.stream.interest_assign import InterestState

            state = InterestState.from_dict(payload)
    elif interest_state_db is not None:
        payload = runtime_store.fetch_interest_state_payload(interest_state_db, run_id=run_id, user_id=user_id)
        if payload is not None:
            from model.stream.interest_assign import InterestState

            state = InterestState.from_dict(payload)
    if state is None and path is not None:
        state = load_interest_state(path)
    if state is None:
        return False

    state.processed_raw_event_ids = sorted(
        {
            *[int(value) for value in state.processed_raw_event_ids],
            *[int(value) for value in processed_raw_event_ids],
        }
    )
    state.pending_raw_event_ids = []
    state.assigned_since_last_refit = 0
    state.outlier_since_last_refit = 0
    state.refit_required = False
    state.refit_request_open = False
    state.refit_reasons = []
    state.updated_at = local_timestamp()
    if interest_state_conn is not None:
        runtime_store.record_interest_state_conn(interest_state_conn, run_id=run_id, state=state)
    elif interest_state_db is not None:
        runtime_store.record_interest_state(interest_state_db, run_id=run_id, state=state)
    if path is not None:
        save_interest_state(state, path)
    return True


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    outputs_dir = root / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    run_id = resolve_model_run_id(outputs_dir, args.run_id, prefer_latest=True)
    run_dir = ensure_experiment_run(root, run_id)
    manifest = load_experiment_manifest(run_dir)
    train_model_config = manifest.get("stages", {}).get("train", {}).get("model_config", {})
    logger, log_path = setup_run_logging("seed_pre_t_state", outputs_dir)

    ratings_path = resolve_path(root, args.ratings)
    item2idx_path = resolve_path(root, args.item2idx)
    state_db_path = resolve_path(root, args.state_db)
    interest_state_db_path = (
        state_db_path if args.interest_state_db is None else resolve_path(root, args.interest_state_db)
    )
    state_dir = None if args.state_dir is None else resolve_path(root, args.state_dir)
    interest_state_dir = None if args.interest_state_dir is None else resolve_path(root, args.interest_state_dir)
    summary_path = resolve_path(root, args.summary) if args.summary else state_db_path.parent / "pre_summary.json"
    seq_len = args.seq_len or int(train_model_config.get("max_len", 100))
    cutoff_ts = parse_ts(args.max_rated_at_exclusive)
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024
    positive_policy = PositivePolicy(
        min_ratings_for_zscore=args.min_ratings_for_zscore,
        z_threshold=args.z_threshold,
        optimistic_cold_start=True,
    )

    if args.reset_state_db and state_db_path.exists():
        state_db_path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = state_db_path.with_name(state_db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
    runtime_store.init_store(state_db_path)
    if interest_state_db_path != state_db_path:
        runtime_store.init_store(interest_state_db_path)

    if args.reset_state_dir and state_dir is not None and state_dir.exists():
        shutil.rmtree(state_dir)
    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True)

    item2idx = load_item2idx(item2idx_path)
    logger.info("Experiment run id: %s", run_id)
    logger.info("Ratings input: %s", ratings_path)
    logger.info("item2idx input: %s", item2idx_path)
    logger.info("State DB: %s", state_db_path)
    logger.info("Legacy state dir: %s", state_dir)
    logger.info("Interest state DB: %s", interest_state_db_path)
    logger.info("Interest state dir: %s", interest_state_dir)
    logger.info("Summary output: %s", summary_path)
    logger.info("Max ratedAt exclusive: %s", args.max_rated_at_exclusive)

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(root),
            "stages": {
                "seed_pre_t_state": {
                    "command": command_line(),
                    "log": file_metadata(log_path, root=root),
                    "inputs": {
                        "ratings": file_metadata(
                            ratings_path,
                            root=root,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "item2idx": file_metadata(
                            item2idx_path,
                            root=root,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "schemas": {
                        "ratings_drop_processed": schema_metadata(
                            root / "schemas/ml32m/processed/ratings_drop_processed.v1.schema.yaml",
                            root=root,
                        ),
                    },
                    "seed_config": {
                        "max_rated_at_exclusive": args.max_rated_at_exclusive,
                        "state_db": relative_or_absolute(root, state_db_path),
                        "interest_state_db": relative_or_absolute(root, interest_state_db_path),
                        "state_dir": None if state_dir is None else relative_or_absolute(root, state_dir),
                        "interest_state_dir": None
                        if interest_state_dir is None
                        else relative_or_absolute(root, interest_state_dir),
                        "summary": relative_or_absolute(root, summary_path),
                        "seq_len": seq_len,
                        "limit_users": args.limit_users,
                        "user_id": args.user_id,
                        "positive_policy": positive_policy.to_dict(),
                        "reset_state_dir": bool(args.reset_state_dir),
                        "reset_state_db": bool(args.reset_state_db),
                        "sqlite_user_event_rows_stored": SEED_USER_EVENT_ROWS_STORED,
                    },
                }
            },
        },
    )

    users_seen = 0
    users_with_pre_t_events = 0
    seeded_users = 0
    interest_marked_users = 0
    raw_events = 0
    positive_events = 0
    active_events = 0
    skipped_unknown_items = 0
    first_rated_at: str | None = None
    last_rated_at: str | None = None

    shared_interest_conn = interest_state_db_path.resolve() == state_db_path.resolve()
    with runtime_store.connect(state_db_path) as state_conn:
        with ratings_path.open("r", encoding="utf-8") as handle:
            for line in tqdm(handle, desc="seed pre-T user states"):
                if not line.strip():
                    continue
                users_seen += 1
                entry = json.loads(line)
                user_id = int(entry["userId"])
                if args.user_id is not None and user_id != int(args.user_id):
                    continue

                filtered_ratings = cutoff_filter(list(entry["ratings"]), cutoff_ts)
                if not filtered_ratings:
                    continue
                users_with_pre_t_events += 1

                state = build_state_from_rating_history(
                    user_id=user_id,
                    ratings=filtered_ratings,
                    seq_len=seq_len,
                    item2idx=item2idx,
                    positive_policy=positive_policy,
                )
                runtime_store.record_user_state_conn(
                    state_conn,
                    run_id=run_id,
                    state=state,
                    include_event_rows=SEED_USER_EVENT_ROWS_STORED,
                )
                if state_dir is not None:
                    save_user_state(state, state_path_for_user(state_dir, user_id))

                active_raw_event_ids = [event.raw_event_id for event in active_positive_events(state)]
                if maybe_mark_interest_processed(
                    interest_state_dir=interest_state_dir,
                    interest_state_db=interest_state_db_path,
                    interest_state_conn=state_conn if shared_interest_conn else None,
                    run_id=run_id,
                    user_id=user_id,
                    processed_raw_event_ids=active_raw_event_ids,
                ):
                    interest_marked_users += 1

                seeded_users += 1
                if seeded_users % 1000 == 0:
                    state_conn.commit()
                raw_events += int(state.stats.get("rawEventCount", 0))
                positive_events += int(state.stats.get("positiveEventCount", 0))
                active_events += int(state.stats.get("activeEventCount", 0))
                skipped_unknown_items += int(state.stats.get("skippedUnknownItems", 0))
                user_first = str(filtered_ratings[0]["ratedAt"])
                user_last = str(filtered_ratings[-1]["ratedAt"])
                first_rated_at = user_first if first_rated_at is None else min(first_rated_at, user_first)
                last_rated_at = user_last if last_rated_at is None else max(last_rated_at, user_last)

                if args.limit_users is not None and seeded_users >= int(args.limit_users):
                    break

    runtime_store.checkpoint(state_db_path)
    summary = {
        "version": SEED_SUMMARY_VERSION,
        "generatedAt": local_timestamp(),
        "runId": run_id,
        "maxRatedAtExclusive": args.max_rated_at_exclusive,
        "stateDb": relative_or_absolute(root, state_db_path),
        "stateStore": {
            "kind": "sqlite",
            "path": relative_or_absolute(root, state_db_path),
            "runId": run_id,
            "userEventRowsStored": SEED_USER_EVENT_ROWS_STORED,
            "counts": runtime_store.count_state_rows(state_db_path, run_id=run_id),
        },
        "stateDir": None if state_dir is None else relative_or_absolute(root, state_dir),
        "interestStateDb": relative_or_absolute(root, interest_state_db_path),
        "interestStateDir": None if interest_state_dir is None else relative_or_absolute(root, interest_state_dir),
        "ratings": relative_or_absolute(root, ratings_path),
        "item2idx": relative_or_absolute(root, item2idx_path),
        "seqLen": seq_len,
        "positivePolicy": positive_policy.to_dict(),
        "usersSeen": users_seen,
        "usersWithPreTEvents": users_with_pre_t_events,
        "seededUsers": seeded_users,
        "interestMarkedUsers": interest_marked_users,
        "rawEvents": raw_events,
        "positiveEvents": positive_events,
        "activeEvents": active_events,
        "skippedUnknownItems": skipped_unknown_items,
        "firstRatedAt": first_rated_at,
        "lastRatedAt": last_rated_at,
        "limitUsers": args.limit_users,
        "userId": args.user_id,
    }
    write_json(summary_path, summary)
    logger.info("Seed summary: %s", summary)

    metric_record = {
        "stage": "seed_pre_t_state",
        "max_rated_at_exclusive": args.max_rated_at_exclusive,
        "users_seen": users_seen,
        "users_with_pre_t_events": users_with_pre_t_events,
        "seeded_users": seeded_users,
        "interest_marked_users": interest_marked_users,
        "raw_events": raw_events,
        "positive_events": positive_events,
        "active_events": active_events,
        "skipped_unknown_items": skipped_unknown_items,
    }
    append_metric(run_dir, metric_record)
    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "seed_pre_t_state": {
                    "data_summary": summary,
                    "outputs": {
                        "state_db": file_metadata(
                            state_db_path,
                            root=root,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "state_dir": None if state_dir is None else relative_or_absolute(root, state_dir),
                        "summary": file_metadata(
                            summary_path,
                            root=root,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "metrics": file_metadata(
                            run_dir / "metrics.jsonl",
                            root=root,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "summary_metrics": metric_record,
                }
            },
        },
    )


if __name__ == "__main__":
    main()
