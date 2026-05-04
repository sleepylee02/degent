from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from tqdm import tqdm

from model.common.dataset import build_genre_map, build_user_sequences, temporal_split, MovieLensDataset
from model.common.sasrec import SASRecCL, augment_sequence, contrastive_loss
from model.common.runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    log_torch_runtime,
    resolve_model_run_id,
    resolve_torch_device,
    schema_metadata,
    set_global_seed,
    setup_run_logging,
    update_experiment_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train SASRecCL and record experiment metadata.")
    parser.add_argument("--run-id", type=str, default=None, help="Experiment run id. Defaults to a timestamp id.")
    parser.add_argument("--hash-inputs", action="store_true", help="Compute SHA256 for input data files.")
    parser.add_argument(
        "--hash-limit-mb",
        type=int,
        default=100,
        help="Max file size for SHA256 hashing. Use -1 for no limit.",
    )
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument("--eval-every", type=int, default=5)
    parser.add_argument("--seq-len", type=int, default=100)
    parser.add_argument("--stride", type=int, default=50)
    parser.add_argument("--min-interactions", type=int, default=200)
    parser.add_argument("--min-activity-days", type=int, default=30)
    parser.add_argument("--d-model", type=int, default=128)
    parser.add_argument("--num-heads", type=int, default=2)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--cl-lambda", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


# =====================
# Trainer
# =====================

class Trainer:
    def __init__(self, model, lr=1e-3, cl_lambda=0.1, device='cuda'):
        self.model     = model.to(device)
        self.optimizer = torch.optim.Adam(model.parameters(), lr=lr)
        self.cl_lambda = cl_lambda
        self.device    = device

    def train_epoch(self, dataloader):
        self.model.train()
        total_loss = 0

        pbar = tqdm(dataloader, desc="train", leave=False)
        for batch in pbar:
            item_id_seq = batch["item_id_seq"].to(self.device)
            genre_seq   = batch["genre_seq"].to(self.device)
            label_seq   = batch["label_seq"].to(self.device)

            # ── CE Loss ──
            ht     = self.model(item_id_seq, genre_seq)
            logits = self.model.score(ht)

            ce_loss = F.cross_entropy(
                logits.view(-1, self.model.num_items + 1),
                label_seq.view(-1),
                ignore_index=0
            )

            # ── Contrastive Loss ──
            aug_item1, aug_genre1 = augment_sequence(item_id_seq, genre_seq)
            aug_item2, aug_genre2 = augment_sequence(item_id_seq, genre_seq)

            h1 = self.model.get_last_hidden(aug_item1, aug_genre1)
            h2 = self.model.get_last_hidden(aug_item2, aug_genre2)
            cl = contrastive_loss(h1, h2)

            loss = ce_loss + self.cl_lambda * cl

            self.optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(self.model.parameters(), 5.0)
            self.optimizer.step()

            total_loss += loss.item()
            pbar.set_postfix(loss=f"{loss.item():.4f}")

        return total_loss / len(dataloader)

    @torch.no_grad()
    def evaluate(self, dataloader, k=10):
        """Recall@K, NDCG@K"""
        self.model.eval()
        recalls, ndcgs = [], []

        for batch in tqdm(dataloader, desc="eval", leave=False):
            item_id_seq = batch["item_id_seq"].to(self.device)
            genre_seq   = batch["genre_seq"].to(self.device)
            label_seq   = batch["label_seq"].to(self.device)

            ht     = self.model(item_id_seq, genre_seq)
            logits = self.model.score(ht)

            lengths     = (item_id_seq != 0).sum(dim=1) - 1
            last_logits = logits[torch.arange(logits.size(0)), lengths]
            last_labels = label_seq[torch.arange(label_seq.size(0)), lengths]

            topk = last_logits.topk(k, dim=-1).indices

            for pred, label in zip(topk, last_labels):
                hit = (pred == label).any().item()
                recalls.append(float(hit))
                if hit:
                    rank = (pred == label).nonzero(as_tuple=True)[0][0].item() + 1
                    ndcgs.append(1.0 / np.log2(rank + 1))
                else:
                    ndcgs.append(0.0)

        return {
            f"Recall@{k}": np.mean(recalls),
            f"NDCG@{k}":   np.mean(ndcgs)
        }


# =====================
# 실행
# =====================

if __name__ == "__main__":
    args = parse_args()
    ROOT        = Path(__file__).resolve().parents[2]
    DATA_DIR    = ROOT / 'data'
    OUTPUTS_DIR = ROOT / 'outputs'
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id      = resolve_model_run_id(OUTPUTS_DIR, args.run_id, prefer_latest=False)
    run_dir     = ensure_experiment_run(ROOT, run_id)
    logger, log_path = setup_run_logging("train", OUTPUTS_DIR)

    movies_path = DATA_DIR / 'movies_processed_drop.csv'
    ratings_path = DATA_DIR / 'ratings_drop_processed.jsonl'
    num_epochs = args.epochs
    batch_size = args.batch_size
    num_workers = args.num_workers
    eval_every = args.eval_every
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024

    logger.info("Experiment run id: %s", run_id)
    logger.info("Experiment metadata directory: %s", run_dir)
    logger.info("Outputs directory: %s", OUTPUTS_DIR)
    logger.info("Movies input: %s", movies_path)
    logger.info("Ratings input: %s", ratings_path)
    logger.info("Seed: %d", args.seed)

    set_global_seed(args.seed)

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(ROOT),
            "stages": {
                "train": {
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
                        "d_model": args.d_model,
                        "num_heads": args.num_heads,
                        "num_layers": args.num_layers,
                        "dropout": args.dropout,
                        "max_len": args.seq_len,
                    },
                    "training_config": {
                        "epochs": num_epochs,
                        "batch_size": batch_size,
                        "num_workers": num_workers,
                        "eval_every": eval_every,
                        "seq_len": args.seq_len,
                        "stride": args.stride,
                        "min_interactions": args.min_interactions,
                        "min_activity_days": args.min_activity_days,
                        "lr": args.lr,
                        "cl_lambda": args.cl_lambda,
                        "seed": args.seed,
                    },
                }
            },
        },
    )

    device, device_label = resolve_torch_device()
    log_torch_runtime(logger, device, device_label)

    # 데이터 로드
    movies = pd.read_csv(movies_path)
    genre_map, all_genres = build_genre_map(movies)
    num_genres            = len(all_genres)
    logger.info("Loaded movies: %d | unique genres: %d", len(movies), num_genres)

    user_sequences = build_user_sequences(
        ratings_path,
        min_interactions=args.min_interactions,
        min_activity_days=args.min_activity_days
    )
    logger.info("Filtered user sequences: %d", len(user_sequences))

    # item id 재매핑 (0: padding)
    all_items = sorted(set(i for seq in user_sequences.values() for i, _ in seq))
    item2idx  = {item: idx + 1 for idx, item in enumerate(all_items)}
    num_items = len(item2idx)

    genre_map_idx = {item2idx[k]: v for k, v in genre_map.items() if k in item2idx}

    # 시간 기준 global split
    train_seq, val_seq, test_seq = temporal_split(user_sequences)
    logger.info(
        "Temporal split users | train=%d val=%d test=%d",
        len(train_seq),
        len(val_seq),
        len(test_seq),
    )

    def remap(sequences):
        return {
            u: [item2idx[i] for i in seq if i in item2idx]
            for u, seq in sequences.items()
        }

    train_seq_idx = remap(train_seq)
    val_seq_idx   = remap(val_seq)

    # Dataset / DataLoader
    train_dataset = MovieLensDataset(train_seq_idx, genre_map_idx, num_genres, seq_len=args.seq_len, stride=args.stride)
    val_dataset   = MovieLensDataset(val_seq_idx,   genre_map_idx, num_genres, seq_len=args.seq_len, stride=args.stride)
    train_generator = torch.Generator()
    train_generator.manual_seed(args.seed)
    train_loader  = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        generator=train_generator,
    )
    val_loader    = DataLoader(val_dataset,   batch_size=batch_size, shuffle=False, num_workers=num_workers)
    logger.info(
        "Dataset summary | items=%d train_samples=%d val_samples=%d train_batches=%d val_batches=%d",
        num_items,
        len(train_dataset),
        len(val_dataset),
        len(train_loader),
        len(val_loader),
    )

    # 모델 초기화
    model  = SASRecCL(
        num_items  = num_items,
        num_genres = num_genres,
        d_model    = args.d_model,
        num_heads  = args.num_heads,
        num_layers = args.num_layers,
        dropout    = args.dropout,
        max_len    = args.seq_len
    )

    trainer = Trainer(model, lr=args.lr, cl_lambda=args.cl_lambda, device=device)
    logger.info(
        "Training config | epochs=%d batch_size=%d seq_len=%d stride=%d cl_lambda=%.3f lr=%.6f",
        num_epochs,
        batch_size,
        args.seq_len,
        args.stride,
        args.cl_lambda,
        args.lr,
    )

    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "train": {
                    "runtime": {
                        "device": str(device),
                        "device_label": device_label,
                        "torch_version": torch.__version__,
                    },
                    "data_summary": {
                        "movies_rows": int(len(movies)),
                        "unique_genres": int(num_genres),
                        "filtered_user_sequences": int(len(user_sequences)),
                        "num_items": int(num_items),
                        "train_users": int(len(train_seq)),
                        "val_users": int(len(val_seq)),
                        "test_users": int(len(test_seq)),
                        "train_samples": int(len(train_dataset)),
                        "val_samples": int(len(val_dataset)),
                        "train_batches": int(len(train_loader)),
                        "val_batches": int(len(val_loader)),
                    },
                }
            },
        },
    )

    # 학습
    final_metrics = {}
    for epoch in range(num_epochs):
        loss = trainer.train_epoch(train_loader)
        logger.info("Epoch %02d/%02d | loss=%.4f", epoch + 1, num_epochs, loss)
        metric_record = {
            "stage": "train",
            "epoch": epoch + 1,
            "loss": float(loss),
        }

        if (epoch + 1) % eval_every == 0:
            metrics = trainer.evaluate(val_loader, k=10)
            logger.info(
                "Validation | epoch=%02d Recall@10=%.4f NDCG@10=%.4f",
                epoch + 1,
                metrics["Recall@10"],
                metrics["NDCG@10"],
            )
            metric_record.update(
                {
                    "recall_at_10": float(metrics["Recall@10"]),
                    "ndcg_at_10": float(metrics["NDCG@10"]),
                }
            )

        append_metric(run_dir, metric_record)
        final_metrics = metric_record

    # 모델 저장
    out_path = OUTPUTS_DIR / 'sasrec_cl.pt'
    torch.save(model.state_dict(), out_path)
    logger.info("Saved model checkpoint: %s", out_path)

    # item2idx 저장 (extract.py에서 동일 vocabulary 재사용)
    item2idx_path = OUTPUTS_DIR / 'item2idx.json'
    with open(item2idx_path, 'w') as f:
        json.dump({str(k): v for k, v in item2idx.items()}, f)
    logger.info("Saved item2idx: %s (%d items)", item2idx_path, len(item2idx))

    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "train": {
                    "outputs": {
                        "checkpoint": file_metadata(
                            out_path,
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
                        "metrics": file_metadata(
                            run_dir / "metrics.jsonl",
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "final_metrics": final_metrics,
                }
            },
        },
    )
