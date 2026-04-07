from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from tqdm import tqdm

from dataset import build_genre_map, build_user_sequences, temporal_split, MovieLensDataset
from model import SASRecCL, augment_sequence, contrastive_loss
from runtime import log_torch_runtime, resolve_torch_device, setup_run_logging


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
    ROOT        = Path(__file__).resolve().parent.parent
    DATA_DIR    = ROOT / 'data'
    OUTPUTS_DIR = ROOT / 'outputs'
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    logger, _   = setup_run_logging("train", OUTPUTS_DIR)

    movies_path = DATA_DIR / 'movies_processed_drop.csv'
    ratings_path = DATA_DIR / 'ratings_drop_processed.jsonl'
    num_epochs = 20
    batch_size = 256
    num_workers = 4
    eval_every = 5

    logger.info("Outputs directory: %s", OUTPUTS_DIR)
    logger.info("Movies input: %s", movies_path)
    logger.info("Ratings input: %s", ratings_path)

    device, device_label = resolve_torch_device()
    log_torch_runtime(logger, device, device_label)

    # 데이터 로드
    movies = pd.read_csv(movies_path)
    genre_map, all_genres = build_genre_map(movies)
    num_genres            = len(all_genres)
    logger.info("Loaded movies: %d | unique genres: %d", len(movies), num_genres)

    user_sequences = build_user_sequences(
        ratings_path,
        min_interactions=200,
        min_activity_days=30
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
    train_dataset = MovieLensDataset(train_seq_idx, genre_map_idx, num_genres, seq_len=100, stride=50)
    val_dataset   = MovieLensDataset(val_seq_idx,   genre_map_idx, num_genres, seq_len=100, stride=50)
    train_loader  = DataLoader(train_dataset, batch_size=batch_size, shuffle=True,  num_workers=num_workers)
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
        d_model    = 128,
        num_heads  = 2,
        num_layers = 2,
        dropout    = 0.2,
        max_len    = 100
    )

    trainer = Trainer(model, lr=1e-3, cl_lambda=0.1, device=device)
    logger.info(
        "Training config | epochs=%d batch_size=%d seq_len=%d stride=%d cl_lambda=%.3f",
        num_epochs,
        batch_size,
        100,
        50,
        0.1,
    )

    # 학습
    for epoch in range(num_epochs):
        loss = trainer.train_epoch(train_loader)
        logger.info("Epoch %02d/%02d | loss=%.4f", epoch + 1, num_epochs, loss)

        if (epoch + 1) % eval_every == 0:
            metrics = trainer.evaluate(val_loader, k=10)
            logger.info(
                "Validation | epoch=%02d Recall@10=%.4f NDCG@10=%.4f",
                epoch + 1,
                metrics["Recall@10"],
                metrics["NDCG@10"],
            )

    # 모델 저장
    out_path = OUTPUTS_DIR / 'sasrec_cl.pt'
    torch.save(model.state_dict(), out_path)
    logger.info("Saved model checkpoint: %s", out_path)
