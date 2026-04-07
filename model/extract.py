import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

from dataset import build_genre_map, build_user_sequences, temporal_split, MovieLensDataset
from model import SASRecCL


@torch.no_grad()
def extract_embeddings(model, dataloader, device, interval=50):
    """
    50개 간격으로 히든스테이트 추출
    padding 제외하고 실제 시점만 추출

    returns:
        all_embeddings: list of (num_timepoints, d_model)
        각 유저의 시점별 히든스테이트
    """
    model.eval()
    all_embeddings = []

    for batch in dataloader:
        item_id_seq = batch["item_id_seq"].to(device)
        genre_seq   = batch["genre_seq"].to(device)

        h = model.encode(item_id_seq, genre_seq)  # (B, L, d_model)

        for i in range(h.size(0)):
            length = (item_id_seq[i] != 0).sum().item()

            # 실제 시점 인덱스 (padding 제외)
            valid_h = h[i, -length:, :]  # (length, d_model)

            # 50개 간격으로 히든스테이트 추출
            indices = list(range(interval - 1, length, interval))
            if not indices:
                indices = [length - 1]  # 시퀀스가 interval보다 짧으면 마지막만

            selected = valid_h[indices, :]  # (num_timepoints, d_model)
            all_embeddings.append(selected.cpu().numpy())

    return all_embeddings  # list of (num_timepoints, d_model)


if __name__ == "__main__":
    # 데이터 로드
    movies = pd.read_csv('data/movies_processed_drop.csv')
    genre_map, all_genres = build_genre_map(movies)
    num_genres            = len(all_genres)

    user_sequences = build_user_sequences(
        'data/ratings_drop_processed.jsonl',
        min_interactions=200,
        min_activity_days=30
    )

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

    train_dataset = MovieLensDataset(train_seq_idx, genre_map_idx, num_genres, seq_len=100, stride=50)
    train_loader  = DataLoader(train_dataset, batch_size=256, shuffle=False, num_workers=4)

    # 학습된 모델 로드
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model  = SASRecCL(
        num_items  = num_items,
        num_genres = num_genres,
        d_model    = 128,
        num_heads  = 2,
        num_layers = 2,
        dropout    = 0.2,
        max_len    = 100
    )
    model.load_state_dict(torch.load('model/sasrec_cl.pt', map_location=device))
    model.to(device)

    # 50개 간격 히든스테이트 추출
    embeddings = extract_embeddings(model, train_loader, device, interval=50)

    # HDBSCAN에 넣을 형태로 concatenate
    all_h = np.concatenate(embeddings, axis=0)  # (전체 시점 수, 128)
    print(f"Embeddings shape: {all_h.shape}")

    # 저장
    np.save('model/embeddings.npy', all_h)
    print("임베딩 저장 완료: model/embeddings.npy")
