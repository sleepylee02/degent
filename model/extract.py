from pathlib import Path
import json

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from dataset import build_genre_map, build_user_sequences, temporal_split, MovieLensDataset
from model import SASRecCL
from runtime import log_torch_runtime, resolve_torch_device, setup_run_logging


@torch.no_grad()
def extract_embeddings(model, dataloader, device, interval=50):
    """
    50개 간격으로 히든스테이트 추출
    유저별, 시점별 정보 함께 저장

    returns:
        all_h:          (전체 시점 수, d_model)
        all_user_ids:   (전체 시점 수,)  각 시점의 유저 id
        all_timepoints: (전체 시점 수,)  유저 전체 타임라인 기준 시점 index
    """
    model.eval()
    all_embeddings = []
    all_user_ids   = []
    all_timepoints = []

    for batch in tqdm(dataloader, desc="extract"):
        item_id_seq = batch["item_id_seq"].to(device)
        genre_seq   = batch["genre_seq"].to(device)
        user_ids    = batch["user_id"]
        start_idxs  = batch["start_idx"]

        h = model.encode(item_id_seq, genre_seq)  # (B, L, d_model)

        for i in range(h.size(0)):
            length  = (item_id_seq[i] != 0).sum().item()
            user_id = int(user_ids[i].item())
            start_idx = int(start_idxs[i].item())

            valid_h = h[i, -length:, :]  # (length, d_model)

            # 50개 간격으로 히든스테이트 추출
            indices = list(range(interval - 1, length, interval))
            if not indices:
                indices = [length - 1]  # interval보다 짧으면 마지막만

            selected       = valid_h[indices, :]  # (num_timepoints, d_model)
            num_timepoints = len(indices)
            global_timepoints = [start_idx + idx for idx in indices]

            all_embeddings.append(selected.cpu().numpy())
            all_user_ids.extend([user_id] * num_timepoints)
            all_timepoints.extend(global_timepoints)

    return (
        np.concatenate(all_embeddings, axis=0),  # (전체 시점 수, d_model)
        np.array(all_user_ids),                  # (전체 시점 수,)
        np.array(all_timepoints),                # (전체 시점 수,)
    )


if __name__ == "__main__":
    ROOT        = Path(__file__).resolve().parent.parent
    DATA_DIR    = ROOT / 'data'
    OUTPUTS_DIR = ROOT / 'outputs'
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    logger, _ = setup_run_logging("extract", OUTPUTS_DIR)

    movies_path     = DATA_DIR / 'movies_processed_drop.csv'
    ratings_path    = DATA_DIR / 'ratings_drop_processed.jsonl'
    checkpoint_path = OUTPUTS_DIR / 'sasrec_cl.pt'
    item2idx_path   = OUTPUTS_DIR / 'item2idx.json'
    batch_size  = 256
    num_workers = 4
    interval    = 10
    stride      = 10

    logger.info("Outputs directory: %s", OUTPUTS_DIR)
    logger.info("Movies input: %s", movies_path)
    logger.info("Ratings input: %s", ratings_path)
    logger.info("Checkpoint input: %s", checkpoint_path)
    logger.info("item2idx input: %s", item2idx_path)

    device, device_label = resolve_torch_device()
    log_torch_runtime(logger, device, device_label)

    # 데이터 로드
    movies = pd.read_csv(movies_path)
    genre_map, all_genres = build_genre_map(movies)
    num_genres            = len(all_genres)
    logger.info("Loaded movies: %d | unique genres: %d", len(movies), num_genres)

    # item2idx: 학습 때와 동일한 vocabulary 재사용
    with open(item2idx_path) as f:
        item2idx = {int(k): v for k, v in json.load(f).items()}
    num_items = len(item2idx)
    logger.info("Loaded item2idx: %d items", num_items)

    genre_map_idx = {item2idx[k]: v for k, v in genre_map.items() if k in item2idx}

    # 클러스터링 대상 유저만 로드 (1000 이상 긍정 상호작용)
    user_sequences = build_user_sequences(
        ratings_path,
        min_interactions=1000,
        min_activity_days=30
    )
    logger.info("Filtered user sequences (>=1000): %d", len(user_sequences))

    train_seq, _, _ = temporal_split(user_sequences)
    train_seq_idx   = {
        u: [item2idx[i] for i in seq if i in item2idx]
        for u, seq in train_seq.items()
    }

    # shuffle=False 필수 (sample_idx 순서 보장)
    train_dataset = MovieLensDataset(train_seq_idx, genre_map_idx, num_genres, seq_len=100, stride=stride)
    train_loader  = DataLoader(train_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers)
    logger.info(
        "Extraction dataset | train_users=%d samples=%d batches=%d items=%d",
        len(train_seq_idx), len(train_dataset), len(train_loader), num_items,
    )

    # 학습된 모델 로드
    model = SASRecCL(
        num_items  = num_items,
        num_genres = num_genres,
        d_model    = 128,
        num_heads  = 2,
        num_layers = 2,
        dropout    = 0.2,
        max_len    = 100
    )
    model.load_state_dict(torch.load(checkpoint_path, map_location=device))
    model.to(device)
    logger.info("Loaded checkpoint onto %s", device_label)

    # 유저별, 시점별 히든스테이트 추출
    all_h, all_user_ids, all_timepoints = extract_embeddings(
        model, train_loader, device, interval=interval
    )
    logger.info("Embeddings shape: %s", all_h.shape)
    logger.info("Unique users in embeddings: %d", len(np.unique(all_user_ids)))

    # npz로 저장 (유저별, 시점별 정보 포함)
    out_path = OUTPUTS_DIR / 'embeddings.npz'
    np.savez(
        out_path,
        embeddings    = all_h,          # (전체 시점 수, 128)
        user_ids      = all_user_ids,   # (전체 시점 수,)
        timepoint_idx = all_timepoints  # (전체 시점 수,)
    )
    logger.info("Saved embeddings: %s", out_path)
