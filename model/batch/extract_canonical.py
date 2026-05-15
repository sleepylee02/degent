from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from model.common.canonical import (
    CanonicalEventDataset,
    load_canonical_event_sequences,
    validate_unique_user_events,
)
from model.common.dataset import build_genre_map
from model.common.sasrec import SASRecCL
from model.common.runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    load_experiment_manifest,
    log_torch_runtime,
    resolve_model_run_id,
    resolve_torch_device,
    schema_metadata,
    setup_run_logging,
    update_experiment_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract canonical event embeddings and record metadata.")
    parser.add_argument("--run-id", type=str, default=None, help="Experiment run id. Defaults to latest train run.")
    parser.add_argument("--hash-inputs", action="store_true", help="Compute SHA256 for input data files.")
    parser.add_argument(
        "--hash-limit-mb",
        type=int,
        default=100,
        help="Max file size for SHA256 hashing. Use -1 for no limit.",
    )
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument("--min-interactions", type=int, default=1000)
    parser.add_argument("--min-activity-days", type=int, default=30)
    parser.add_argument(
        "--max-rated-at-exclusive",
        type=str,
        default=None,
        help="Use only ratings with ratedAt strictly before this UTC ISO timestamp.",
    )
    parser.add_argument("--limit-users", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None, help="Extract only this user (skips min-interactions filter).")
    parser.add_argument("--movies", type=Path, default=Path("data/movies_processed_drop.csv"))
    parser.add_argument("--ratings", type=Path, default=Path("data/ratings_drop_processed.jsonl"))
    parser.add_argument("--checkpoint", type=Path, default=Path("outputs/sasrec_cl.pt"))
    parser.add_argument("--item2idx", type=Path, default=Path("outputs/item2idx.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/canonical_embeddings.npz"))
    parser.add_argument("--seq-len", type=int, default=None)
    parser.add_argument("--d-model", type=int, default=None)
    parser.add_argument("--num-heads", type=int, default=None)
    parser.add_argument("--num-layers", type=int, default=None)
    parser.add_argument("--dropout", type=float, default=None)
    return parser.parse_args()


def resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


@torch.no_grad()
def extract_canonical_embeddings(model, dataloader, device):
    model.eval()
    embeddings = []
    user_ids = []
    event_idx = []
    movie_ids = []
    rated_at_ts = []
    rated_at_iso = []
    history_len = []
    context_start_idx = []

    for batch in tqdm(dataloader, desc="extract canonical"):
        item_id_seq = batch["item_id_seq"].to(device)
        genre_seq = batch["genre_seq"].to(device)
        selected = model.get_last_hidden(item_id_seq, genre_seq)

        embeddings.append(selected.cpu().numpy())
        user_ids.extend(batch["user_id"].numpy().tolist())
        event_idx.extend(batch["event_idx"].numpy().tolist())
        movie_ids.extend(batch["movie_id"].numpy().tolist())
        rated_at_ts.extend(batch["rated_at_ts"].numpy().tolist())
        rated_at_iso.extend(batch["rated_at_iso"])
        history_len.extend(batch["history_len"].numpy().tolist())
        context_start_idx.extend(batch["context_start_idx"].numpy().tolist())

    if not embeddings:
        raise ValueError("No canonical embeddings were extracted.")

    return {
        "embeddings": np.concatenate(embeddings, axis=0).astype(np.float32),
        "user_ids": np.array(user_ids, dtype=np.int64),
        "event_idx": np.array(event_idx, dtype=np.int64),
        "movie_ids": np.array(movie_ids, dtype=np.int64),
        "rated_at_ts": np.array(rated_at_ts, dtype=np.float64),
        "rated_at_iso": np.array(rated_at_iso, dtype=str),
        "history_len": np.array(history_len, dtype=np.int64),
        "context_start_idx": np.array(context_start_idx, dtype=np.int64),
    }


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
    logger, log_path = setup_run_logging("extract_canonical", OUTPUTS_DIR)

    movies_path = resolve_path(ROOT, args.movies)
    ratings_path = resolve_path(ROOT, args.ratings)
    checkpoint_path = resolve_path(ROOT, args.checkpoint)
    item2idx_path = resolve_path(ROOT, args.item2idx)
    out_path = resolve_path(ROOT, args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    seq_len = args.seq_len or int(train_model_config.get("max_len", 100))
    d_model = args.d_model or int(train_model_config.get("d_model", 128))
    num_heads = args.num_heads or int(train_model_config.get("num_heads", 2))
    num_layers = args.num_layers or int(train_model_config.get("num_layers", 2))
    dropout = args.dropout if args.dropout is not None else float(train_model_config.get("dropout", 0.2))
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024

    logger.info("Experiment run id: %s", run_id)
    logger.info("Experiment metadata directory: %s", run_dir)
    logger.info("Outputs directory: %s", OUTPUTS_DIR)
    logger.info("Movies input: %s", movies_path)
    logger.info("Ratings input: %s", ratings_path)
    logger.info("Checkpoint input: %s", checkpoint_path)
    logger.info("item2idx input: %s", item2idx_path)
    logger.info("Canonical output: %s", out_path)
    logger.info("Max ratedAt exclusive: %s", args.max_rated_at_exclusive)

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(ROOT),
            "stages": {
                "extract_canonical": {
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
                    "extraction_config": {
                        "batch_size": args.batch_size,
                        "num_workers": args.num_workers,
                        "min_interactions": args.min_interactions,
                        "min_activity_days": args.min_activity_days,
                        "max_rated_at_exclusive": args.max_rated_at_exclusive,
                        "limit_users": args.limit_users,
                        "user_id": args.user_id,
                        "embedding_contract": "canonical_event_v1",
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
    logger.info("Loaded item2idx: %d items", num_items)

    genre_map_idx = {item2idx[k]: v for k, v in genre_map.items() if k in item2idx}
    user_events, load_stats = load_canonical_event_sequences(
        ratings_path,
        item2idx,
        min_interactions=1 if args.user_id is not None else args.min_interactions,
        min_activity_days=0 if args.user_id is not None else args.min_activity_days,
        limit_users=args.limit_users,
        user_id=args.user_id,
        max_rated_at_exclusive=args.max_rated_at_exclusive,
    )
    logger.info("Canonical load stats: %s", load_stats.to_dict())

    dataset = CanonicalEventDataset(user_events, genre_map_idx, num_genres, seq_len=seq_len)
    dataloader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
    )
    logger.info(
        "Canonical dataset | users=%d samples=%d batches=%d items=%d",
        len(user_events),
        len(dataset),
        len(dataloader),
        num_items,
    )

    model = SASRecCL(
        num_items=num_items,
        num_genres=num_genres,
        d_model=d_model,
        num_heads=num_heads,
        num_layers=num_layers,
        dropout=dropout,
        max_len=seq_len,
    )
    model.load_state_dict(torch.load(checkpoint_path, map_location=device))
    model.to(device)
    logger.info("Loaded checkpoint onto %s", device_label)

    arrays = extract_canonical_embeddings(model, dataloader, device)
    unique_user_events = validate_unique_user_events(arrays["user_ids"], arrays["event_idx"])
    nan_embedding_rows = int(np.isnan(arrays["embeddings"]).any(axis=1).sum())
    max_history_len = int(arrays["history_len"].max()) if len(arrays["history_len"]) else 0
    invalid_context_rows = int((arrays["context_start_idx"] > arrays["event_idx"]).sum())

    if not unique_user_events:
        raise ValueError("Canonical uniqueness failed: duplicate (user_id, event_idx) rows found.")
    if nan_embedding_rows:
        raise ValueError(f"Canonical embeddings contain {nan_embedding_rows} NaN rows.")
    if max_history_len > seq_len:
        raise ValueError(f"history_len exceeded seq_len: max={max_history_len} seq_len={seq_len}")
    if invalid_context_rows:
        raise ValueError(f"context_start_idx > event_idx for {invalid_context_rows} rows.")

    np.savez(out_path, **arrays)
    logger.info("Saved canonical embeddings: %s", out_path)

    metric_record = {
        "stage": "extract_canonical",
        "embedding_rows": int(arrays["embeddings"].shape[0]),
        "embedding_dim": int(arrays["embeddings"].shape[1]),
        "unique_users": int(len(np.unique(arrays["user_ids"]))),
        "unique_user_events": bool(unique_user_events),
        "nan_embedding_rows": nan_embedding_rows,
        "max_history_len": max_history_len,
        "invalid_context_rows": invalid_context_rows,
        **load_stats.to_dict(),
    }
    append_metric(run_dir, metric_record)
    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "extract_canonical": {
                    "runtime": {
                        "device": str(device),
                        "device_label": device_label,
                        "torch_version": torch.__version__,
                    },
                    "data_summary": {
                        "movies_rows": int(len(movies)),
                        "unique_genres": int(num_genres),
                        "num_items": int(num_items),
                        "canonical_users": int(len(user_events)),
                        "samples": int(len(dataset)),
                        "batches": int(len(dataloader)),
                        **load_stats.to_dict(),
                    },
                    "outputs": {
                        "canonical_embeddings": file_metadata(
                            out_path,
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
