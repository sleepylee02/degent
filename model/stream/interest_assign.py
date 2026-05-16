from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import argparse
import json

import numpy as np

import model.stream.runtime_store as runtime_store
from model.common.runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    local_timestamp,
    resolve_model_run_id,
    setup_run_logging,
    update_experiment_manifest,
)


INTEREST_STATE_VERSION = "online_interest_state.v1"


@dataclass
class Interest:
    interest_id: int
    vector: list[float]
    assigned_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    source: str | None = None
    top_genres: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        timestamp = local_timestamp()
        if not self.created_at:
            self.created_at = timestamp
        if not self.updated_at:
            self.updated_at = timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "interestId": self.interest_id,
            "vector": self.vector,
            "assignedCount": self.assigned_count,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "source": self.source,
            "topGenres": self.top_genres,
        }

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "Interest":
        return cls(
            interest_id=int(item.get("interestId", item.get("interest_id"))),
            vector=[float(value) for value in item["vector"]],
            assigned_count=int(item.get("assignedCount", item.get("assigned_count", 0))),
            created_at=str(item.get("createdAt", item.get("created_at", ""))),
            updated_at=str(item.get("updatedAt", item.get("updated_at", ""))),
            source=item.get("source"),
            top_genres=list(item.get("topGenres", item.get("top_genres", []))),
        )


@dataclass
class InterestState:
    user_id: int
    embedding_dim: int
    interests: list[Interest]
    pending_raw_event_ids: list[int]
    processed_raw_event_ids: list[int]
    assigned_since_last_refit: int = 0
    outlier_since_last_refit: int = 0
    refit_required: bool = False
    refit_request_open: bool = False
    refit_reasons: list[str] | None = None
    last_assigned_at: str | None = None
    updated_at: str | None = None
    version: str = INTEREST_STATE_VERSION

    def __post_init__(self) -> None:
        self.pending_raw_event_ids = sorted(set(int(value) for value in self.pending_raw_event_ids))
        self.processed_raw_event_ids = sorted(set(int(value) for value in self.processed_raw_event_ids))
        if self.refit_reasons is None:
            self.refit_reasons = []
        if self.updated_at is None:
            self.updated_at = local_timestamp()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "userId": self.user_id,
            "embeddingDim": self.embedding_dim,
            "interests": [interest.to_dict() for interest in self.interests],
            "pendingRawEventIds": self.pending_raw_event_ids,
            "processedRawEventIds": self.processed_raw_event_ids,
            "assignedSinceLastRefit": self.assigned_since_last_refit,
            "outlierSinceLastRefit": self.outlier_since_last_refit,
            "refitRequired": self.refit_required,
            "refitRequestOpen": self.refit_request_open,
            "refitReasons": self.refit_reasons or [],
            "lastAssignedAt": self.last_assigned_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "InterestState":
        return cls(
            version=str(item.get("version", INTEREST_STATE_VERSION)),
            user_id=int(item.get("userId", item.get("user_id"))),
            embedding_dim=int(item.get("embeddingDim", item.get("embedding_dim"))),
            interests=[Interest.from_dict(interest) for interest in item.get("interests", [])],
            pending_raw_event_ids=[int(value) for value in item.get("pendingRawEventIds", [])],
            processed_raw_event_ids=[int(value) for value in item.get("processedRawEventIds", [])],
            assigned_since_last_refit=int(item.get("assignedSinceLastRefit", 0)),
            outlier_since_last_refit=int(item.get("outlierSinceLastRefit", 0)),
            refit_required=bool(item.get("refitRequired", False)),
            refit_request_open=bool(item.get("refitRequestOpen", False)),
            refit_reasons=[str(value) for value in item.get("refitReasons", [])],
            last_assigned_at=item.get("lastAssignedAt"),
            updated_at=item.get("updatedAt"),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assign active online embeddings to interest states and record refit requests."
    )
    parser.add_argument("--run-id", type=str, default=None, help="Experiment run id. Defaults to latest train run.")
    parser.add_argument("--hash-inputs", action="store_true", help="Compute SHA256 for input embedding files.")
    parser.add_argument(
        "--hash-limit-mb",
        type=int,
        default=100,
        help="Max file size for SHA256 hashing. Use -1 for no limit.",
    )
    parser.add_argument("--embeddings", type=Path, default=Path("outputs/stream/online_embeddings.npz"))
    parser.add_argument("--state-db", type=Path, default=None, help="SQLite interest state store. Defaults to --runtime-db.")
    parser.add_argument("--seed-state-db", type=Path, default=None, help="Optional pre-T SQLite seed state store.")
    parser.add_argument("--seed-run-id", type=str, default=None, help="Run id to read from --seed-state-db.")
    parser.add_argument(
        "--interest-state-dir",
        type=Path,
        default=None,
        help="Optional legacy per-user JSON interest state directory.",
    )
    parser.add_argument("--assignments", type=Path, default=Path("outputs/stream/interest_assignments.jsonl"))
    parser.add_argument("--refit-requests", type=Path, default=Path("outputs/stream/refit_requests.jsonl"))
    parser.add_argument("--similarity-threshold", type=float, default=0.2)
    parser.add_argument("--refit-min-events", type=int, default=20)
    parser.add_argument("--assign-trigger-count", type=int, default=50)
    parser.add_argument("--outlier-trigger-count", type=int, default=10)
    parser.add_argument("--runtime-db", type=Path, default=None, help="Optional SQLite runtime/state store.")
    parser.add_argument("--event-id", type=int, default=None, help="Replay event id for runtime DB linkage.")
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Read changed active embedding rows from --runtime-db instead of --embeddings NPZ.",
    )
    return parser.parse_args()


def resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def state_path_for_user(state_dir: Path, user_id: int) -> Path:
    return state_dir / f"{int(user_id)}.json"


def load_interest_state(path: Path) -> InterestState | None:
    if not path.exists():
        return None
    return InterestState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def load_interest_state_from_db(db_path: Path | None, *, run_id: str, user_id: int) -> InterestState | None:
    if db_path is None:
        return None
    payload = runtime_store.fetch_interest_state_payload(db_path, run_id=run_id, user_id=user_id)
    if payload is None:
        return None
    return InterestState.from_dict(payload)


def load_interest_state_with_seed(
    *,
    primary_db: Path | None,
    primary_run_id: str,
    seed_db: Path | None,
    seed_run_id: str,
    state_dir: Path | None,
    user_id: int,
) -> InterestState | None:
    state = load_interest_state_from_db(primary_db, run_id=primary_run_id, user_id=user_id)
    if state is not None:
        return state
    state = load_interest_state_from_db(seed_db, run_id=seed_run_id, user_id=user_id)
    if state is not None:
        return state
    if state_dir is not None:
        return load_interest_state(state_path_for_user(state_dir, user_id))
    return None


def save_interest_state(state: InterestState, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def make_empty_interest_state(user_id: int, embedding_dim: int) -> InterestState:
    return InterestState(
        user_id=int(user_id),
        embedding_dim=int(embedding_dim),
        interests=[],
        pending_raw_event_ids=[],
        processed_raw_event_ids=[],
    )


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_online_embedding_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    data = np.load(path)
    required = [
        "embeddings",
        "user_ids",
        "raw_event_ids",
        "event_idx",
        "movie_ids",
        "rated_at_ts",
        "rated_at_iso",
        "history_len",
        "context_start_idx",
    ]
    missing = [key for key in required if key not in data.files]
    if missing:
        raise ValueError(f"Online embeddings are missing required keys: {missing}")

    embeddings = data["embeddings"].astype(np.float32)
    if embeddings.ndim != 2:
        raise ValueError(f"embeddings must be 2D, got {embeddings.shape}")
    row_count, embedding_dim = embeddings.shape
    status = data["status"] if "status" in data.files else np.array(["active"] * row_count)

    rows = []
    for position in range(row_count):
        if str(status[position]) != "active":
            continue
        rows.append(
            {
                "embedding": embeddings[position],
                "userId": int(data["user_ids"][position]),
                "rawEventId": int(data["raw_event_ids"][position]),
                "eventIdx": int(data["event_idx"][position]),
                "movieId": int(data["movie_ids"][position]),
                "ratedAtTs": float(data["rated_at_ts"][position]),
                "ratedAt": str(data["rated_at_iso"][position]),
                "historyLen": int(data["history_len"][position]),
                "contextStartIdx": int(data["context_start_idx"][position]),
            }
        )
    return rows, int(embedding_dim)


def group_rows_by_user(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["userId"]), []).append(row)
    for user_rows in grouped.values():
        user_rows.sort(key=lambda item: (item["ratedAtTs"], item["eventIdx"], item["rawEventId"]))
    return grouped


def embedding_dim_from_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    return int(np.asarray(rows[0]["embedding"], dtype=np.float32).shape[0])


def cosine_similarities(embedding: np.ndarray, interests: list[Interest]) -> np.ndarray:
    matrix = np.array([interest.vector for interest in interests], dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        return np.array([], dtype=np.float32)

    embedding_norm = float(np.linalg.norm(embedding))
    matrix_norm = np.linalg.norm(matrix, axis=1)
    denom = np.maximum(embedding_norm * matrix_norm, 1e-8)
    return (matrix @ embedding) / denom


def ensure_interest_dimensions(state: InterestState) -> None:
    for interest in state.interests:
        if len(interest.vector) != state.embedding_dim:
            raise ValueError(
                f"Interest vector dimension mismatch for user {state.user_id}, "
                f"interest {interest.interest_id}: {len(interest.vector)} != {state.embedding_dim}"
            )


def append_unique(values: list[int], value: int) -> None:
    if int(value) not in set(values):
        values.append(int(value))
        values.sort()


def remove_if_present(values: list[int], value: int) -> None:
    try:
        values.remove(int(value))
    except ValueError:
        return


def update_refit_state(
    state: InterestState,
    *,
    refit_min_events: int,
    assign_trigger_count: int,
    outlier_trigger_count: int,
) -> list[str]:
    reasons: list[str] = []
    if not state.interests and len(state.pending_raw_event_ids) >= refit_min_events:
        reasons.append("no_interest_pending_events")
    if state.interests and len(state.pending_raw_event_ids) >= refit_min_events:
        reasons.append("pending_events")
    if state.assigned_since_last_refit >= assign_trigger_count:
        reasons.append("assigned_since_last_refit")
    if state.outlier_since_last_refit >= outlier_trigger_count:
        reasons.append("outlier_since_last_refit")

    merged = sorted(set([*(state.refit_reasons or []), *reasons]))
    state.refit_reasons = merged
    state.refit_required = bool(merged)
    return reasons


def assign_user_rows(
    state: InterestState,
    rows: list[dict[str, Any]],
    *,
    similarity_threshold: float,
    refit_min_events: int,
    assign_trigger_count: int,
    outlier_trigger_count: int,
    run_id: str,
    skip_processed_records: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    ensure_interest_dimensions(state)
    now = local_timestamp()
    assignment_records: list[dict[str, Any]] = []

    processed = set(state.processed_raw_event_ids)
    pending = set(state.pending_raw_event_ids)

    for row in rows:
        raw_event_id = int(row["rawEventId"])
        base_record = {
            "recordedAt": now,
            "runId": run_id,
            "userId": state.user_id,
            "rawEventId": raw_event_id,
            "eventIdx": int(row["eventIdx"]),
            "movieId": int(row["movieId"]),
            "ratedAt": row["ratedAt"],
            "historyLen": int(row["historyLen"]),
            "contextStartIdx": int(row["contextStartIdx"]),
        }

        if raw_event_id in processed:
            if skip_processed_records:
                continue
            assignment_records.append({**base_record, "status": "already_processed"})
            continue

        if not state.interests:
            append_unique(state.pending_raw_event_ids, raw_event_id)
            pending.add(raw_event_id)
            status = "pending_refit_required" if len(state.pending_raw_event_ids) >= refit_min_events else "pending_no_interest"
            assignment_records.append(
                {
                    **base_record,
                    "status": status,
                    "reason": "no_interest_state",
                    "assignedInterestId": None,
                    "similarity": None,
                }
            )
            continue

        similarities = cosine_similarities(row["embedding"], state.interests)
        if similarities.size == 0 or not np.isfinite(similarities).all():
            append_unique(state.pending_raw_event_ids, raw_event_id)
            if raw_event_id not in pending:
                state.outlier_since_last_refit += 1
            pending.add(raw_event_id)
            assignment_records.append(
                {
                    **base_record,
                    "status": "outlier",
                    "reason": "invalid_similarity",
                    "assignedInterestId": None,
                    "similarity": None,
                }
            )
            continue

        best_position = int(np.argmax(similarities))
        best_similarity = float(similarities[best_position])
        best_interest = state.interests[best_position]

        if best_similarity < similarity_threshold:
            append_unique(state.pending_raw_event_ids, raw_event_id)
            if raw_event_id not in pending:
                state.outlier_since_last_refit += 1
            pending.add(raw_event_id)
            assignment_records.append(
                {
                    **base_record,
                    "status": "outlier",
                    "reason": "low_similarity",
                    "assignedInterestId": best_interest.interest_id,
                    "similarity": best_similarity,
                    "similarityThreshold": similarity_threshold,
                }
            )
            continue

        remove_if_present(state.pending_raw_event_ids, raw_event_id)
        pending.discard(raw_event_id)
        append_unique(state.processed_raw_event_ids, raw_event_id)
        processed.add(raw_event_id)
        best_interest.assigned_count += 1
        best_interest.updated_at = now
        state.assigned_since_last_refit += 1
        state.last_assigned_at = now
        assignment_records.append(
            {
                **base_record,
                "status": "assigned",
                "assignedInterestId": best_interest.interest_id,
                "similarity": best_similarity,
                "similarityThreshold": similarity_threshold,
            }
        )

    new_refit_reasons = update_refit_state(
        state,
        refit_min_events=refit_min_events,
        assign_trigger_count=assign_trigger_count,
        outlier_trigger_count=outlier_trigger_count,
    )
    state.updated_at = local_timestamp()

    refit_request = None
    if state.refit_required and not state.refit_request_open:
        refit_request = {
            "recordedAt": state.updated_at,
            "runId": run_id,
            "userId": state.user_id,
            "status": "open",
            "reasons": state.refit_reasons or new_refit_reasons,
            "pendingRawEventCount": len(state.pending_raw_event_ids),
            "assignedSinceLastRefit": state.assigned_since_last_refit,
            "outlierSinceLastRefit": state.outlier_since_last_refit,
            "refitMinEvents": refit_min_events,
            "assignTriggerCount": assign_trigger_count,
            "outlierTriggerCount": outlier_trigger_count,
        }
        state.refit_request_open = True

    return assignment_records, refit_request


if __name__ == "__main__":
    args = parse_args()
    ROOT = Path(__file__).resolve().parents[2]
    OUTPUTS_DIR = ROOT / "outputs"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = resolve_model_run_id(OUTPUTS_DIR, args.run_id, prefer_latest=True)
    run_dir = ensure_experiment_run(ROOT, run_id)
    logger, log_path = setup_run_logging("interest_assign", OUTPUTS_DIR)

    embeddings_path = resolve_path(ROOT, args.embeddings)
    runtime_db_path = None if args.runtime_db is None else resolve_path(ROOT, args.runtime_db)
    state_db_path = runtime_db_path if args.state_db is None else resolve_path(ROOT, args.state_db)
    seed_state_db_path = None if args.seed_state_db is None else resolve_path(ROOT, args.seed_state_db)
    seed_run_id = args.seed_run_id or run_id
    interest_state_dir = None if args.interest_state_dir is None else resolve_path(ROOT, args.interest_state_dir)
    if state_db_path is None and interest_state_dir is None:
        interest_state_dir = OUTPUTS_DIR / "stream" / "interest_states"
    assignments_path = resolve_path(ROOT, args.assignments)
    refit_requests_path = resolve_path(ROOT, args.refit_requests)
    if args.use_cache and (runtime_db_path is None or args.event_id is None):
        raise ValueError("--use-cache requires --runtime-db and --event-id.")
    if state_db_path is not None:
        runtime_store.init_store(state_db_path)
    if runtime_db_path is not None:
        runtime_store.init_store(runtime_db_path)
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024

    logger.info("Experiment run id: %s", run_id)
    logger.info("Experiment metadata directory: %s", run_dir)
    logger.info("Online embeddings input: %s", embeddings_path)
    logger.info("Use embedding cache: %s", args.use_cache)
    logger.info("State DB: %s", state_db_path)
    logger.info("Seed state DB: %s run_id=%s", seed_state_db_path, seed_run_id)
    logger.info("Legacy interest state directory: %s", interest_state_dir)
    logger.info("Assignments output: %s", assignments_path)
    logger.info("Refit requests output: %s", refit_requests_path)

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(ROOT),
            "stages": {
                "interest_assign": {
                    "command": command_line(),
                    "log": file_metadata(log_path, root=ROOT),
                    "inputs": {
                        "online_embeddings": None
                        if args.use_cache
                        else file_metadata(
                            embeddings_path,
                            root=ROOT,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "embedding_cache": None
                        if not args.use_cache
                        else {
                            "runtime_db": relative_or_absolute(ROOT, runtime_db_path),
                            "event_id": args.event_id,
                        },
                    },
                    "assignment_config": {
                        "state_db": None
                        if state_db_path is None
                        else relative_or_absolute(ROOT, state_db_path),
                        "seed_state_db": None
                        if seed_state_db_path is None
                        else relative_or_absolute(ROOT, seed_state_db_path),
                        "seed_run_id": seed_run_id,
                        "interest_state_dir": None
                        if interest_state_dir is None
                        else relative_or_absolute(ROOT, interest_state_dir),
                        "assignments": relative_or_absolute(ROOT, assignments_path),
                        "refit_requests": relative_or_absolute(ROOT, refit_requests_path),
                        "similarity_threshold": args.similarity_threshold,
                        "refit_min_events": args.refit_min_events,
                        "assign_trigger_count": args.assign_trigger_count,
                        "outlier_trigger_count": args.outlier_trigger_count,
                    },
                }
            },
        },
    )

    if args.use_cache:
        rows = runtime_store.fetch_embedding_cache_changed_rows(
            runtime_db_path,
            run_id=run_id,
            event_id=args.event_id,
        )
        embedding_dim = embedding_dim_from_rows(rows)
    else:
        rows, embedding_dim = load_online_embedding_rows(embeddings_path)
    grouped_rows = group_rows_by_user(rows)
    logger.info("Loaded active online embedding rows: %d users=%d dim=%d", len(rows), len(grouped_rows), embedding_dim)

    all_assignment_records: list[dict[str, Any]] = []
    refit_request_records: list[dict[str, Any]] = []
    user_summaries: list[dict[str, Any]] = []

    for user_id, user_rows in sorted(grouped_rows.items()):
        state_path = None if interest_state_dir is None else state_path_for_user(interest_state_dir, user_id)
        state = load_interest_state_with_seed(
            primary_db=state_db_path,
            primary_run_id=run_id,
            seed_db=seed_state_db_path,
            seed_run_id=seed_run_id,
            state_dir=interest_state_dir,
            user_id=user_id,
        )
        if state is None:
            state = make_empty_interest_state(user_id, embedding_dim)
        if state.embedding_dim != embedding_dim:
            raise ValueError(f"Embedding dim mismatch for user {user_id}: {state.embedding_dim} != {embedding_dim}")

        before_pending = len(state.pending_raw_event_ids)
        before_processed = len(state.processed_raw_event_ids)
        assignment_records, refit_request = assign_user_rows(
            state,
            user_rows,
            similarity_threshold=args.similarity_threshold,
            refit_min_events=args.refit_min_events,
            assign_trigger_count=args.assign_trigger_count,
            outlier_trigger_count=args.outlier_trigger_count,
            run_id=run_id,
            skip_processed_records=args.use_cache,
        )
        if state_path is not None:
            save_interest_state(state, state_path)
        if state_db_path is not None:
            runtime_store.record_interest_state(
                state_db_path,
                run_id=run_id,
                state=state,
                state_path=None if state_path is None else relative_or_absolute(ROOT, state_path),
            )
        if runtime_db_path is not None and runtime_db_path != state_db_path:
            runtime_store.record_interest_state(
                runtime_db_path,
                run_id=run_id,
                state=state,
                state_path=None if state_path is None else relative_or_absolute(ROOT, state_path),
            )

        all_assignment_records.extend(assignment_records)
        if refit_request is not None:
            refit_request_records.append(refit_request)

        statuses: dict[str, int] = {}
        for record in assignment_records:
            statuses[record["status"]] = statuses.get(record["status"], 0) + 1
        summary = {
            "userId": user_id,
            "inputRows": len(user_rows),
            "interestCount": len(state.interests),
            "pendingBefore": before_pending,
            "pendingAfter": len(state.pending_raw_event_ids),
            "processedBefore": before_processed,
            "processedAfter": len(state.processed_raw_event_ids),
            "refitRequired": state.refit_required,
            "refitReasons": state.refit_reasons or [],
            "statuses": statuses,
            "stateDb": None if state_db_path is None else relative_or_absolute(ROOT, state_db_path),
            "statePath": None if state_path is None else relative_or_absolute(ROOT, state_path),
        }
        user_summaries.append(summary)
        logger.info("Processed interest state: %s", summary)

    append_jsonl(assignments_path, all_assignment_records)
    if refit_request_records:
        append_jsonl(refit_requests_path, refit_request_records)
    if runtime_db_path is not None:
        runtime_store.record_assignments(
            runtime_db_path,
            run_id=run_id,
            records=all_assignment_records,
            event_id=args.event_id,
        )
        runtime_store.open_refit_requests(runtime_db_path, run_id=run_id, records=refit_request_records)

    status_counts: dict[str, int] = {}
    for record in all_assignment_records:
        status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1

    metric_record = {
        "stage": "interest_assign",
        "input_rows": len(rows),
        "processed_users": len(grouped_rows),
        "assignment_records": len(all_assignment_records),
        "refit_requests": len(refit_request_records),
        "status_counts": status_counts,
        "embedding_dim": embedding_dim,
    }
    append_metric(run_dir, metric_record)

    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "interest_assign": {
                    "data_summary": {
                        "active_embedding_rows": len(rows),
                        "processed_users": len(grouped_rows),
                        "user_summaries": user_summaries,
                    },
                    "outputs": {
                        "assignments": file_metadata(
                            assignments_path,
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "refit_requests": file_metadata(
                            refit_requests_path,
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "metrics": file_metadata(
                            run_dir / "metrics.jsonl",
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "summary_metrics": metric_record,
                }
            },
        },
    )
