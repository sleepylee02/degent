from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import hashlib
import json

import numpy as np
import pandas as pd
import torch

import model.stream.runtime_store as runtime_store
from model.common.canonical import build_canonical_item_window
from model.common.dataset import build_genre_map
from model.common.runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    load_experiment_manifest,
    local_timestamp,
    log_torch_runtime,
    resolve_model_run_id,
    resolve_torch_device,
    schema_metadata,
    setup_run_logging,
    update_experiment_manifest,
)
from model.common.sasrec import SASRecCL
from model.stream.state import (
    OnlineUserState,
    PositiveEvent,
    PositivePolicy,
    active_positive_events,
    append_rating_event,
    build_state_from_rating_history,
    canonical_events_from_state,
    load_user_state,
    make_empty_state,
    save_user_state,
    state_path_for_user,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest rating events, refresh user positive state, and extract online canonical embeddings."
    )
    parser.add_argument("--run-id", type=str, default=None, help="Experiment run id. Defaults to latest train run.")
    parser.add_argument("--hash-inputs", action="store_true", help="Compute SHA256 for input data files.")
    parser.add_argument(
        "--hash-limit-mb",
        type=int,
        default=100,
        help="Max file size for SHA256 hashing. Use -1 for no limit.",
    )
    parser.add_argument("--movies", type=Path, default=Path("data/movies_processed_drop.csv"))
    parser.add_argument("--ratings", type=Path, default=Path("data/ratings_drop_processed.jsonl"))
    parser.add_argument("--checkpoint", type=Path, default=Path("outputs/sasrec_cl.pt"))
    parser.add_argument("--item2idx", type=Path, default=Path("outputs/item2idx.json"))
    parser.add_argument("--state-db", type=Path, default=None, help="SQLite state store. Defaults to --runtime-db.")
    parser.add_argument("--seed-state-db", type=Path, default=None, help="Optional pre-T SQLite seed state store.")
    parser.add_argument("--seed-run-id", type=str, default=None, help="Run id to read from --seed-state-db.")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Optional legacy per-user JSON user state directory.",
    )
    parser.add_argument("--output", type=Path, default=Path("outputs/stream/online_embeddings.npz"))
    parser.add_argument(
        "--cache-embeddings",
        action="store_true",
        help="Upsert active online embeddings into --runtime-db for replay runtime consumers.",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Do not write --output NPZ. Requires --cache-embeddings and --runtime-db.",
    )
    parser.add_argument("--event-log", type=Path, default=Path("outputs/stream/online_embedding_events.jsonl"))
    parser.add_argument("--runtime-db", type=Path, default=None, help="Optional SQLite runtime/state store.")
    parser.add_argument("--event-id", type=int, default=None, help="Replay event id for runtime DB linkage.")
    parser.add_argument("--bootstrap-user-id", type=int, default=None)
    parser.add_argument("--event-json", type=str, default=None, help="Single event JSON with userId/movieId/rating/ratedAt.")
    parser.add_argument("--event-jsonl", type=Path, default=None, help="JSONL events with userId/movieId/rating/ratedAt.")
    parser.add_argument("--user-id", type=int, default=None, help="Single event userId.")
    parser.add_argument("--movie-id", type=int, default=None, help="Single event movieId.")
    parser.add_argument("--rating", type=float, default=None, help="Single event rating.")
    parser.add_argument("--rated-at", type=str, default=None, help="Single event UTC ISO 8601 timestamp.")
    parser.add_argument("--min-ratings-for-zscore", type=int, default=3)
    parser.add_argument("--z-threshold", type=float, default=0.0)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--compare-canonical", type=Path, default=None)
    parser.add_argument("--compare-atol", type=float, default=1e-5)
    parser.add_argument("--compare-rtol", type=float, default=1e-5)
    parser.add_argument("--seq-len", type=int, default=None)
    parser.add_argument("--d-model", type=int, default=None)
    parser.add_argument("--num-heads", type=int, default=None)
    parser.add_argument("--num-layers", type=int, default=None)
    parser.add_argument("--dropout", type=float, default=None)
    return parser.parse_args()


def resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def event_value(event: dict[str, Any], camel: str, snake: str) -> Any:
    if camel in event:
        return event[camel]
    if snake in event:
        return event[snake]
    raise KeyError(f"Event is missing required field {camel}/{snake}: {event}")


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": int(event_value(event, "userId", "user_id")),
        "movie_id": int(event_value(event, "movieId", "movie_id")),
        "rating": float(event["rating"]),
        "rated_at": str(event_value(event, "ratedAt", "rated_at")),
        "raw_event_id": event.get("rawEventId", event.get("raw_event_id")),
    }


def collect_input_events(args: argparse.Namespace, root: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if args.event_json is not None:
        events.append(normalize_event(json.loads(args.event_json)))

    if args.event_jsonl is not None:
        event_jsonl = resolve_path(root, args.event_jsonl)
        with event_jsonl.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    events.append(normalize_event(json.loads(line)))

    single_event_fields = [args.user_id, args.movie_id, args.rating, args.rated_at]
    if any(value is not None for value in single_event_fields):
        if not all(value is not None for value in single_event_fields):
            raise ValueError("--user-id, --movie-id, --rating, and --rated-at must be provided together.")
        events.append(
            {
                "user_id": int(args.user_id),
                "movie_id": int(args.movie_id),
                "rating": float(args.rating),
                "rated_at": str(args.rated_at),
                "raw_event_id": None,
            }
        )

    return events


def load_bootstrap_user(ratings_path: Path, user_id: int) -> list[dict[str, Any]]:
    with ratings_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            if int(entry["userId"]) == int(user_id):
                return list(entry["ratings"])
    raise ValueError(f"bootstrap userId not found: {user_id}")


def get_genre_vec(item_id: int, genre_map_idx: dict[int, list[int]], num_genres: int) -> torch.Tensor:
    vec = torch.zeros(num_genres)
    for genre_idx in genre_map_idx.get(item_id, []):
        vec[genre_idx] = 1.0
    return vec


@torch.no_grad()
def extract_state_embeddings(
    state: OnlineUserState,
    model: SASRecCL,
    *,
    genre_map_idx: dict[int, list[int]],
    num_genres: int,
    seq_len: int,
    d_model: int,
    batch_size: int,
    device: torch.device,
    target_raw_event_ids: set[int] | None = None,
) -> dict[str, np.ndarray]:
    model.eval()
    active_events = active_positive_events(state)
    canonical_events = canonical_events_from_state(state)
    if not active_events:
        return empty_embedding_arrays(d_model)

    embeddings: list[np.ndarray] = []
    user_ids: list[int] = []
    raw_event_ids: list[int] = []
    event_idx: list[int] = []
    movie_ids: list[int] = []
    rated_at_ts: list[float] = []
    rated_at_iso: list[str] = []
    history_len: list[int] = []
    context_start_idx: list[int] = []
    status: list[str] = []

    item_seq_batch: list[list[int]] = []
    genre_seq_batch: list[torch.Tensor] = []
    metadata_batch: list[tuple[PositiveEvent, int, int]] = []

    def flush_batch() -> None:
        if not item_seq_batch:
            return

        item_id_seq = torch.tensor(item_seq_batch, dtype=torch.long, device=device)
        genre_seq = torch.stack(genre_seq_batch).to(device)
        selected = model.get_last_hidden(item_id_seq, genre_seq)
        embeddings.append(selected.cpu().numpy().astype(np.float32))

        for event, batch_context_start_idx, batch_history_len in metadata_batch:
            user_ids.append(event.user_id)
            raw_event_ids.append(event.raw_event_id)
            event_idx.append(event.event_idx)
            movie_ids.append(event.movie_id)
            rated_at_ts.append(event.rated_at_ts)
            rated_at_iso.append(event.rated_at_iso)
            history_len.append(batch_history_len)
            context_start_idx.append(batch_context_start_idx)
            status.append(event.status)

        item_seq_batch.clear()
        genre_seq_batch.clear()
        metadata_batch.clear()

    for event_position, event in enumerate(active_events):
        if target_raw_event_ids is not None and int(event.raw_event_id) not in target_raw_event_ids:
            continue
        item_ids, batch_context_start_idx, batch_history_len = build_canonical_item_window(
            canonical_events,
            event_position,
            seq_len,
        )
        pad_len = seq_len - len(item_ids)
        item_id_seq = item_ids + [0] * pad_len
        genre_seq = torch.stack([get_genre_vec(item_id, genre_map_idx, num_genres) for item_id in item_id_seq])

        item_seq_batch.append(item_id_seq)
        genre_seq_batch.append(genre_seq)
        metadata_batch.append((event, batch_context_start_idx, batch_history_len))

        if len(item_seq_batch) >= batch_size:
            flush_batch()

    flush_batch()

    if not embeddings:
        return empty_embedding_arrays(d_model)

    return {
        "embeddings": np.concatenate(embeddings, axis=0).astype(np.float32),
        "user_ids": np.array(user_ids, dtype=np.int64),
        "raw_event_ids": np.array(raw_event_ids, dtype=np.int64),
        "event_idx": np.array(event_idx, dtype=np.int64),
        "movie_ids": np.array(movie_ids, dtype=np.int64),
        "rated_at_ts": np.array(rated_at_ts, dtype=np.float64),
        "rated_at_iso": np.array(rated_at_iso, dtype=str),
        "history_len": np.array(history_len, dtype=np.int64),
        "context_start_idx": np.array(context_start_idx, dtype=np.int64),
        "status": np.array(status, dtype=str),
    }


def empty_embedding_arrays(d_model: int) -> dict[str, np.ndarray]:
    return {
        "embeddings": np.empty((0, d_model), dtype=np.float32),
        "user_ids": np.array([], dtype=np.int64),
        "raw_event_ids": np.array([], dtype=np.int64),
        "event_idx": np.array([], dtype=np.int64),
        "movie_ids": np.array([], dtype=np.int64),
        "rated_at_ts": np.array([], dtype=np.float64),
        "rated_at_iso": np.array([], dtype=str),
        "history_len": np.array([], dtype=np.int64),
        "context_start_idx": np.array([], dtype=np.int64),
        "status": np.array([], dtype=str),
    }


def concatenate_arrays(items: list[dict[str, np.ndarray]], d_model: int) -> dict[str, np.ndarray]:
    if not items:
        return empty_embedding_arrays(d_model)

    result: dict[str, np.ndarray] = {}
    for key in items[0]:
        arrays = [item[key] for item in items if len(item[key]) or key == "embeddings"]
        if key == "embeddings":
            non_empty = [array for array in arrays if array.shape[0] > 0]
            result[key] = np.concatenate(non_empty, axis=0) if non_empty else np.empty((0, d_model), dtype=np.float32)
        else:
            result[key] = np.concatenate(arrays, axis=0) if arrays else empty_embedding_arrays(d_model)[key]
    return result


def active_embedding_signatures(state: OnlineUserState, seq_len: int) -> dict[int, dict[str, Any]]:
    active_events = active_positive_events(state)
    canonical_events = canonical_events_from_state(state)
    signatures: dict[int, dict[str, Any]] = {}
    for event_position, event in enumerate(active_events):
        item_ids, context_start_idx, history_len = build_canonical_item_window(
            canonical_events,
            event_position,
            seq_len,
        )
        payload = {
            "rawEventId": int(event.raw_event_id),
            "eventIdx": int(event.event_idx),
            "movieId": int(event.movie_id),
            "itemIdx": int(event.item_idx),
            "ratedAtTs": float(event.rated_at_ts),
            "historyLen": int(history_len),
            "contextStartIdx": int(context_start_idx),
            "itemWindow": [int(item_id) for item_id in item_ids],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signatures[int(event.raw_event_id)] = {
            "hash": hashlib.sha1(encoded).hexdigest(),
            "payload": payload,
        }
    return signatures


def validate_online_arrays(arrays: dict[str, np.ndarray], seq_len: int) -> dict[str, int | bool]:
    row_count = int(arrays["embeddings"].shape[0])
    for key, value in arrays.items():
        if key == "embeddings":
            continue
        if len(value) != row_count:
            raise ValueError(f"Online output length mismatch for {key}: {len(value)} != {row_count}")

    nan_embedding_rows = int(np.isnan(arrays["embeddings"]).any(axis=1).sum()) if row_count else 0
    max_history_len = int(arrays["history_len"].max()) if row_count else 0
    invalid_history_rows = int((arrays["history_len"] > seq_len).sum()) if row_count else 0
    invalid_context_rows = int((arrays["context_start_idx"] > arrays["event_idx"]).sum()) if row_count else 0
    active_rows = int((arrays["status"] == "active").sum()) if row_count else 0
    return {
        "row_count": row_count,
        "nan_embedding_rows": nan_embedding_rows,
        "max_history_len": max_history_len,
        "invalid_history_rows": invalid_history_rows,
        "invalid_context_rows": invalid_context_rows,
        "all_rows_active": active_rows == row_count,
    }


def compare_with_canonical(
    online_arrays: dict[str, np.ndarray],
    canonical_path: Path,
    *,
    atol: float,
    rtol: float,
) -> dict[str, Any]:
    canonical = np.load(canonical_path)
    canonical_index = {
        (int(user_id), int(event_idx)): position
        for position, (user_id, event_idx) in enumerate(zip(canonical["user_ids"], canonical["event_idx"]))
    }
    matched = 0
    allclose = 0
    max_abs_diff = 0.0
    for position, (user_id, event_idx) in enumerate(zip(online_arrays["user_ids"], online_arrays["event_idx"])):
        canonical_position = canonical_index.get((int(user_id), int(event_idx)))
        if canonical_position is None:
            continue
        matched += 1
        diff = np.abs(online_arrays["embeddings"][position] - canonical["embeddings"][canonical_position])
        max_abs_diff = max(max_abs_diff, float(diff.max()))
        if np.allclose(
            online_arrays["embeddings"][position],
            canonical["embeddings"][canonical_position],
            atol=atol,
            rtol=rtol,
        ):
            allclose += 1

    return {
        "canonicalPath": str(canonical_path),
        "matchedRows": matched,
        "allcloseRows": allclose,
        "allMatchedRowsClose": bool(matched > 0 and matched == allclose),
        "maxAbsDiff": max_abs_diff,
        "atol": atol,
        "rtol": rtol,
    }


def append_event_log(path: Path, record: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_user_state_from_db(db_path: Path | None, *, run_id: str, user_id: int) -> OnlineUserState | None:
    if db_path is None:
        return None
    payload = runtime_store.fetch_user_state_payload(db_path, run_id=run_id, user_id=user_id)
    if payload is None:
        return None
    return OnlineUserState.from_dict(payload)


def load_user_state_with_seed(
    *,
    primary_db: Path | None,
    primary_run_id: str,
    seed_db: Path | None,
    seed_run_id: str,
    state_dir: Path | None,
    user_id: int,
) -> OnlineUserState | None:
    state = load_user_state_from_db(primary_db, run_id=primary_run_id, user_id=user_id)
    if state is not None:
        return state
    state = load_user_state_from_db(seed_db, run_id=seed_run_id, user_id=user_id)
    if state is not None:
        return state
    if state_dir is not None:
        return load_user_state(state_path_for_user(state_dir, user_id))
    return None


if __name__ == "__main__":
    args = parse_args()
    ROOT = Path(__file__).resolve().parents[2]
    DATA_DIR = ROOT / "data"
    OUTPUTS_DIR = ROOT / "outputs"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = resolve_model_run_id(OUTPUTS_DIR, args.run_id, prefer_latest=True)
    run_dir = ensure_experiment_run(ROOT, run_id)
    manifest = load_experiment_manifest(run_dir)
    train_model_config = manifest.get("stages", {}).get("train", {}).get("model_config", {})
    logger, log_path = setup_run_logging("extract_online", OUTPUTS_DIR)

    movies_path = resolve_path(ROOT, args.movies)
    ratings_path = resolve_path(ROOT, args.ratings)
    checkpoint_path = resolve_path(ROOT, args.checkpoint)
    item2idx_path = resolve_path(ROOT, args.item2idx)
    runtime_db_path = None if args.runtime_db is None else resolve_path(ROOT, args.runtime_db)
    state_db_path = runtime_db_path if args.state_db is None else resolve_path(ROOT, args.state_db)
    seed_state_db_path = None if args.seed_state_db is None else resolve_path(ROOT, args.seed_state_db)
    seed_run_id = args.seed_run_id or run_id
    state_dir = None if args.state_dir is None else resolve_path(ROOT, args.state_dir)
    if state_db_path is None and state_dir is None:
        state_dir = OUTPUTS_DIR / "stream" / "user_states"
    output_path = resolve_path(ROOT, args.output)
    event_log_path = resolve_path(ROOT, args.event_log)
    compare_canonical_path = None if args.compare_canonical is None else resolve_path(ROOT, args.compare_canonical)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if state_db_path is not None:
        runtime_store.init_store(state_db_path)
    if runtime_db_path is not None:
        runtime_store.init_store(runtime_db_path)
    if args.cache_only and (not args.cache_embeddings or runtime_db_path is None):
        raise ValueError("--cache-only requires --cache-embeddings and --runtime-db.")
    if args.cache_embeddings and runtime_db_path is None:
        raise ValueError("--cache-embeddings requires --runtime-db.")
    if args.cache_embeddings and args.event_id is None:
        raise ValueError("--cache-embeddings requires --event-id.")

    seq_len = args.seq_len or int(train_model_config.get("max_len", 100))
    d_model = args.d_model or int(train_model_config.get("d_model", 128))
    num_heads = args.num_heads or int(train_model_config.get("num_heads", 2))
    num_layers = args.num_layers or int(train_model_config.get("num_layers", 2))
    dropout = args.dropout if args.dropout is not None else float(train_model_config.get("dropout", 0.2))
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024
    positive_policy = PositivePolicy(
        min_ratings_for_zscore=args.min_ratings_for_zscore,
        z_threshold=args.z_threshold,
        optimistic_cold_start=True,
    )

    logger.info("Experiment run id: %s", run_id)
    logger.info("Experiment metadata directory: %s", run_dir)
    logger.info("State DB: %s", state_db_path)
    logger.info("Seed state DB: %s run_id=%s", seed_state_db_path, seed_run_id)
    logger.info("Legacy state directory: %s", state_dir)
    logger.info("Online embedding output: %s", output_path)
    logger.info("Cache embeddings: %s cache_only=%s", args.cache_embeddings, args.cache_only)
    logger.info("Event log output: %s", event_log_path)

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(ROOT),
            "stages": {
                "extract_online": {
                    "command": command_line(),
                    "log": file_metadata(log_path, root=ROOT),
                    "inputs": {
                        "movies": file_metadata(
                            movies_path,
                            root=ROOT,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "ratings": file_metadata(
                            ratings_path,
                            root=ROOT,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "checkpoint": file_metadata(
                            checkpoint_path,
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "item2idx": file_metadata(
                            item2idx_path,
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "schemas": {
                        "movies_processed": schema_metadata(
                            ROOT / "schemas/ml32m/processed/movies_processed.v2.schema.yaml",
                            root=ROOT,
                        ),
                        "ratings_drop_processed": schema_metadata(
                            ROOT / "schemas/ml32m/processed/ratings_drop_processed.v1.schema.yaml",
                            root=ROOT,
                        ),
                    },
                    "model_config": {
                        "architecture": "SASRecCL",
                        "d_model": d_model,
                        "num_heads": num_heads,
                        "num_layers": num_layers,
                        "dropout": dropout,
                        "max_len": seq_len,
                    },
                    "online_config": {
                        "state_db": None
                        if state_db_path is None
                        else str(state_db_path.relative_to(ROOT) if state_db_path.is_relative_to(ROOT) else state_db_path),
                        "seed_state_db": None
                        if seed_state_db_path is None
                        else str(
                            seed_state_db_path.relative_to(ROOT)
                            if seed_state_db_path.is_relative_to(ROOT)
                            else seed_state_db_path
                        ),
                        "seed_run_id": seed_run_id,
                        "state_dir": None
                        if state_dir is None
                        else str(state_dir.relative_to(ROOT) if state_dir.is_relative_to(ROOT) else state_dir),
                        "output": str(output_path.relative_to(ROOT) if output_path.is_relative_to(ROOT) else output_path),
                        "cache_embeddings": args.cache_embeddings,
                        "cache_only": args.cache_only,
                        "event_log": str(event_log_path.relative_to(ROOT) if event_log_path.is_relative_to(ROOT) else event_log_path),
                        "bootstrap_user_id": args.bootstrap_user_id,
                        "event_jsonl": None if args.event_jsonl is None else str(args.event_jsonl),
                        "min_ratings_for_zscore": args.min_ratings_for_zscore,
                        "z_threshold": args.z_threshold,
                        "batch_size": args.batch_size,
                    },
                }
            },
        },
    )

    device, device_label = resolve_torch_device()
    log_torch_runtime(logger, device, device_label)

    movies = pd.read_csv(movies_path)
    genre_map, all_genres = build_genre_map(movies)
    num_genres = len(all_genres)
    logger.info("Loaded movies: %d | unique genres: %d", len(movies), num_genres)

    with item2idx_path.open("r", encoding="utf-8") as handle:
        item2idx = {int(k): int(v) for k, v in json.load(handle).items()}
    num_items = len(item2idx)
    genre_map_idx = {item2idx[k]: v for k, v in genre_map.items() if k in item2idx}
    logger.info("Loaded item2idx: %d items", num_items)

    model = SASRecCL(
        num_items=num_items,
        num_genres=num_genres,
        d_model=d_model,
        num_heads=num_heads,
        num_layers=num_layers,
        dropout=dropout,
        max_len=seq_len,
    )
    model.load_state_dict(torch.load(checkpoint_path, map_location=device, weights_only=True))
    model.to(device)
    logger.info("Loaded checkpoint onto %s", device_label)

    states: dict[int, OnlineUserState] = {}
    if args.bootstrap_user_id is not None:
        bootstrap_ratings = load_bootstrap_user(ratings_path, args.bootstrap_user_id)
        states[args.bootstrap_user_id] = build_state_from_rating_history(
            user_id=args.bootstrap_user_id,
            ratings=bootstrap_ratings,
            seq_len=seq_len,
            item2idx=item2idx,
            positive_policy=positive_policy,
        )
        logger.info(
            "Bootstrapped user %d from %d raw ratings",
            args.bootstrap_user_id,
            len(bootstrap_ratings),
        )

    input_events = collect_input_events(args, ROOT)
    for event in input_events:
        user_id = int(event["user_id"])
        state = states.get(user_id)
        if state is None:
            state = load_user_state_with_seed(
                primary_db=state_db_path,
                primary_run_id=run_id,
                seed_db=seed_state_db_path,
                seed_run_id=seed_run_id,
                state_dir=state_dir,
                user_id=user_id,
            )
        if state is None:
            state = make_empty_state(user_id, seq_len=seq_len, positive_policy=positive_policy)
        else:
            state.positive_policy = positive_policy
            state.seq_len = seq_len

        append_rating_event(
            state,
            movie_id=int(event["movie_id"]),
            rating=float(event["rating"]),
            rated_at_iso=str(event["rated_at"]),
            item2idx=item2idx,
            raw_event_id=None if event.get("raw_event_id") is None else int(event["raw_event_id"]),
        )
        states[user_id] = state

    if not states:
        raise ValueError(
            "No users to process. Provide --bootstrap-user-id, --event-json, --event-jsonl, "
            "or --user-id/--movie-id/--rating/--rated-at."
        )

    state_arrays = []
    state_summaries = []
    cache_totals = {
        "activeRows": 0,
        "changedRows": 0,
        "insertedRows": 0,
        "updatedRows": 0,
        "inactivatedRows": 0,
    }
    for user_id, state in sorted(states.items()):
        state_path = None
        if state_dir is not None:
            state_path = save_user_state(state, state_path_for_user(state_dir, user_id))
        if state_db_path is not None:
            runtime_store.record_user_state(
                state_db_path,
                run_id=run_id,
                state=state,
                state_path=None if state_path is None else str(state_path),
            )
        if runtime_db_path is not None and runtime_db_path != state_db_path:
            runtime_store.record_user_state(
                runtime_db_path,
                run_id=run_id,
                state=state,
                state_path=None if state_path is None else str(state_path),
            )
        target_raw_event_ids = None
        signatures = {}
        if args.cache_embeddings:
            signatures = active_embedding_signatures(state, seq_len)
            cached_signatures = runtime_store.fetch_active_embedding_cache_signatures(
                runtime_db_path,
                run_id=run_id,
                user_id=user_id,
            )
            target_raw_event_ids = {
                raw_event_id
                for raw_event_id, signature in signatures.items()
                if cached_signatures.get(raw_event_id) != signature["hash"]
            }

        arrays = extract_state_embeddings(
            state,
            model,
            genre_map_idx=genre_map_idx,
            num_genres=num_genres,
            seq_len=seq_len,
            d_model=d_model,
            batch_size=args.batch_size,
            device=device,
            target_raw_event_ids=target_raw_event_ids,
        )
        state_arrays.append(arrays)
        cache_summary = None
        if args.cache_embeddings:
            cache_summary = runtime_store.upsert_active_embedding_cache(
                runtime_db_path,
                run_id=run_id,
                user_id=user_id,
                event_id=args.event_id,
                arrays=arrays,
                signatures=signatures,
                active_raw_event_ids=set(signatures),
            )
            for key, value in cache_summary.items():
                cache_totals[key] += int(value)
        summary = {
            "userId": user_id,
            "stateDb": None
            if state_db_path is None
            else str(state_db_path.relative_to(ROOT) if state_db_path.is_relative_to(ROOT) else state_db_path),
            "statePath": None
            if state_path is None
            else str(state_path.relative_to(ROOT) if state_path.is_relative_to(ROOT) else state_path),
            **(state.stats or {}),
            "embeddingRows": int(arrays["embeddings"].shape[0]),
            **({"embeddingCache": cache_summary} if cache_summary is not None else {}),
        }
        state_summaries.append(summary)
        logger.info("Processed user state: %s", summary)

    online_arrays = concatenate_arrays(state_arrays, d_model)
    validation = validate_online_arrays(online_arrays, seq_len)
    if validation["nan_embedding_rows"]:
        raise ValueError(f"Online embeddings contain {validation['nan_embedding_rows']} NaN rows.")
    if validation["invalid_history_rows"]:
        raise ValueError(f"history_len exceeded seq_len for {validation['invalid_history_rows']} rows.")
    if validation["invalid_context_rows"]:
        raise ValueError(f"context_start_idx > event_idx for {validation['invalid_context_rows']} rows.")
    if not validation["all_rows_active"]:
        raise ValueError("Online embedding output contains non-active rows.")

    output_arrays = online_arrays
    if not args.cache_only:
        if args.cache_embeddings:
            full_arrays = [
                extract_state_embeddings(
                    state,
                    model,
                    genre_map_idx=genre_map_idx,
                    num_genres=num_genres,
                    seq_len=seq_len,
                    d_model=d_model,
                    batch_size=args.batch_size,
                    device=device,
                )
                for state in states.values()
            ]
            output_arrays = concatenate_arrays(full_arrays, d_model)
        np.savez(output_path, **output_arrays)
        logger.info("Saved online embeddings: %s", output_path)
    else:
        logger.info("Skipped online embeddings NPZ write because --cache-only is enabled.")

    if runtime_db_path is not None and not args.cache_only:
        runtime_store.record_embedding_snapshot(
            runtime_db_path,
            run_id=run_id,
            kind="online_embeddings",
            path=str(output_path),
            arrays=output_arrays,
            scope="touched_users",
        )

    compare_summary = None
    if compare_canonical_path is not None:
        compare_summary = compare_with_canonical(
            online_arrays,
            compare_canonical_path,
            atol=args.compare_atol,
            rtol=args.compare_rtol,
        )
        logger.info("Canonical comparison: %s", compare_summary)

    metric_record = {
        "stage": "extract_online",
        "processed_users": len(states),
        "input_events": len(input_events),
        "embedding_rows": int(online_arrays["embeddings"].shape[0]),
        "embedding_dim": int(online_arrays["embeddings"].shape[1]) if online_arrays["embeddings"].ndim == 2 else 0,
        "unique_users": int(len(np.unique(online_arrays["user_ids"]))) if len(online_arrays["user_ids"]) else 0,
        "cache_embeddings": args.cache_embeddings,
        "cache_only": args.cache_only,
        "embedding_cache": cache_totals,
        **validation,
    }
    if compare_summary is not None:
        metric_record["canonical_compare"] = compare_summary

    event_log_record = {
        "recordedAt": local_timestamp(),
        "runId": run_id,
        "stage": "extract_online",
        "stateSummaries": state_summaries,
        "metrics": metric_record,
    }
    append_event_log(event_log_path, event_log_record)
    append_metric(run_dir, metric_record)

    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "extract_online": {
                    "runtime": {
                        "device": str(device),
                        "device_label": device_label,
                        "torch_version": torch.__version__,
                    },
                    "data_summary": {
                        "movies_rows": int(len(movies)),
                        "unique_genres": int(num_genres),
                        "num_items": int(num_items),
                        "processed_users": len(states),
                        "state_summaries": state_summaries,
                    },
                    "outputs": {
                        "online_embeddings": file_metadata(
                            output_path,
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "event_log": file_metadata(
                            event_log_path,
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
