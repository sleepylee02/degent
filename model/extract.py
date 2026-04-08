from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from dataset import build_genre_map, build_user_sequences, temporal_split, MovieLensDataset
from model import SASRecCL
from runtime import log_torch_runtime, resolve_torch_device, setup_run_logging


@torch.no_grad()
def extract_embeddings(model, dataloader, device, user_ids_per_sample, interval=50):
    """
    50개 간격으로 히든스테이트 추출
    유저별, 시점별 정보 함께 저장

    returns:
        all_h:          (전체 시점 수, d_model)
        all_user_ids:   (전체 시점 수,)  각 시점의 유저 id
        all_timepoints: (전체 시점 수,)  유저 내 시점 순서 (0, 1, 2, ...)
    """
    model.eval()
    all_embeddings = []
    all_user_ids   = []
    all_timepoints = []

    sample_idx = 0

    for batch in tqdm(dataloader, desc="extract"):
        item_id_seq = batch["item_id_seq"].to(device)
        genre_seq   = batch["genre_seq"].to(device)

        h = model.encode(item_id_seq, genre_seq)  # (B, L, d_model)

        for i in range(h.size(0)):
            length  = (item_id_seq[i] != 0).sum().item()
            user_id = user_ids_per_sample[sample_idx]
            sample_idx += 1

            valid_h = h[i, -length:, :]  # (length, d_model)

            # 50개 간격으로 히든스테이트 추출
            indices = list(range(interval - 1, length, interval))
            if not indices:
                indices = [length - 1]  # interval보다 짧으면 마지막만

            selected       = valid_h[indices, :]  # (num_timepoints, d_model)
            num_timepoints = len(indices)

            all_embeddings.append(selected.cpu().numpy())
            all_user_ids.extend([user_id] * num_timepoints)
            all_timepoints.extend(list(range(num_timepoints)))

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
    batch_size  = 256
    num_workers = 4
    interval    = 50

    logger.info("Outputs directory: %s", OUTPUTS_DIR)
    logger.info("Movies input: %s", movies_path)
    logger.info("Ratings input: %s", ratings_path)
    logger.info("Checkpoint input: %s", checkpoint_path)

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

    # item id 재매핑
    all_items = sorted(set(i for seq in user_sequences.values() for i, _ in seq))
    item2idx  = {item: idx + 1 for idx, item in enumerate(all_items)}
    num_items = len(item2idx)

    genre_map_idx = {item2idx[k]: v for k, v in genre_map.items() if k in item2idx}

    train_seq, _, _ = temporal_split(user_sequences)
    train_seq_idx   = {
        u: [item2idx[i] for i in seq if i in item2idx]
        for u, seq in train_seq.items()
    }

    # shuffle=False 필수 (sample_idx 순서 보장)
    train_dataset = MovieLensDataset(train_seq_idx, genre_map_idx, num_genres, seq_len=100, stride=50)
    train_loader  = DataLoader(train_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers)
    logger.info(
        "Extraction dataset | train_users=%d samples=%d batches=%d items=%d",
        len(train_seq_idx), len(train_dataset), len(train_loader), num_items,
    )

    # 샘플 순서대로 user_id 리스트 생성
    # MovieLensDataset.data에 (user_id, items) 형태로 저장돼 있어야 함
    # dataset.py의 self.data를 [(user_id, items), ...] 로 변경 필요
    user_ids_per_sample = [user_id for user_id, _ in train_dataset.data]
    logger.info("User IDs per sample prepared: %d", len(user_ids_per_sample))

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
        model, train_loader, device, user_ids_per_sample, interval=interval
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