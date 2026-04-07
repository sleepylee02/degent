import json
import numpy as np
import pandas as pd
from datetime import datetime
from collections import defaultdict
from torch.utils.data import Dataset
import torch


# =====================
# 전처리 유틸
# =====================

def parse_ts(ratedAt: str) -> float:
    """UTC ISO 8601 문자열 → float timestamp"""
    return datetime.fromisoformat(ratedAt.replace('Z', '+00:00')).timestamp()


def build_genre_map(movies_df):
    """
    movies_processed_drop.csv 로드
    genres 컬럼: json_string 형태 ex) "[\"Action\",\"Comedy\"]"
    returns:
        genre_map  {movieId: [genre_idx, ...]}
        all_genres [genre_name, ...]
    """
    all_genres = set()
    for genres in movies_df['genres']:
        for g in json.loads(genres):
            all_genres.add(g)
    all_genres = sorted(list(all_genres))
    genre2idx  = {g: i for i, g in enumerate(all_genres)}

    genre_map = {}
    for _, row in movies_df.iterrows():
        idxs = [genre2idx[g] for g in json.loads(row['genres']) if g in genre2idx]
        genre_map[row['movieId']] = idxs

    return genre_map, all_genres


def build_user_sequences(jsonl_path, min_interactions=200, min_activity_days=30):
    """
    ratings_drop_processed.jsonl 로드
    필터링 기준:
        - activity span 30일 이상
        - 긍정 상호작용 200개 이상
    z-score 표준화 후 z > 0인 항목만 긍정 상호작용으로 정의
    returns:
        user_sequences {userId: [(movieId, timestamp), ...]}
    """
    user_sequences = {}

    with open(jsonl_path) as f:
        for line in f:
            entry   = json.loads(line)
            user_id = entry['userId']
            ratings = entry['ratings']

            # activity span 필터링 (30일 이상)
            first_ts = parse_ts(entry['firstRatedAt'])
            last_ts  = parse_ts(entry['lastRatedAt'])
            activity_days = (last_ts - first_ts) / 86400
            if activity_days < min_activity_days:
                continue

            # z-score 표준화 후 긍정 상호작용만 추출
            rating_values = np.array([r['rating'] for r in ratings])
            z_scores = (rating_values - rating_values.mean()) / (rating_values.std() + 1e-8)

            positive = [
                (r['movieId'], parse_ts(r['ratedAt']))
                for r, z in zip(ratings, z_scores)
                if z > 0
            ]

            # 긍정 상호작용 200개 이상 필터링
            if len(positive) < min_interactions:
                continue

            positive.sort(key=lambda x: x[1])
            user_sequences[user_id] = positive

    return user_sequences


def temporal_split(user_sequences, test_ratio=0.1, val_ratio=0.1):
    """
    시간 기준 global split
    전체 interaction을 timestamp로 정렬 후 비율로 분할
    """
    all_interactions = []
    for user_id, seq in user_sequences.items():
        for item_id, ts in seq:
            all_interactions.append((ts, user_id, item_id))

    all_interactions.sort(key=lambda x: x[0])
    n = len(all_interactions)

    train_end = int(n * (1 - test_ratio - val_ratio))
    val_end   = int(n * (1 - test_ratio))

    train_set = set((u, i) for _, u, i in all_interactions[:train_end])
    val_set   = set((u, i) for _, u, i in all_interactions[train_end:val_end])
    test_set  = set((u, i) for _, u, i in all_interactions[val_end:])

    train_sequences, val_sequences, test_sequences = {}, {}, {}

    for user_id, seq in user_sequences.items():
        items      = [item_id for item_id, _ in seq]
        timestamps = [ts      for _,       ts in seq]

        train_items = [i for i, ts in zip(items, timestamps) if (user_id, i) in train_set]
        val_items   = [i for i, ts in zip(items, timestamps) if (user_id, i) in val_set]
        test_items  = [i for i, ts in zip(items, timestamps) if (user_id, i) in test_set]

        if len(train_items) >= 3:
            train_sequences[user_id] = train_items
        if val_items:
            val_sequences[user_id]   = train_items + val_items
        if test_items:
            test_sequences[user_id]  = train_items + val_items + test_items

    return train_sequences, val_sequences, test_sequences


# =====================
# Dataset
# =====================

class MovieLensDataset(Dataset):
    def __init__(self, user_sequences, genre_map, num_genres, seq_len=100, stride=50):
        """
        user_sequences: {userId: [movieId, ...]}  (item2idx 재매핑 완료된 상태)
        genre_map:      {movieId(remapped): [genre_idx, ...]}
        seq_len:        시퀀스 길이 (default: 100)
        stride:         슬라이딩 윈도우 간격 (default: 50)
        """
        self.data       = []
        self.genre_map  = genre_map
        self.num_genres = num_genres
        self.seq_len    = seq_len

        for user_id, items in user_sequences.items():
            if len(items) < 3:
                continue
            if len(items) <= seq_len + 1:
                self.data.append(items)
            else:
                # 슬라이딩 윈도우로 여러 샘플 생성
                for start in range(0, len(items) - seq_len, stride):
                    self.data.append(items[start:start + seq_len + 1])

    def __len__(self):
        return len(self.data)

    def get_genre_vec(self, item_id):
        vec = torch.zeros(self.num_genres)
        for g in self.genre_map.get(item_id, []):
            vec[g] = 1.0
        return vec

    def __getitem__(self, idx):
        items = self.data[idx]

        if len(items) > self.seq_len + 1:
            items = items[-(self.seq_len + 1):]

        input_items = items[:-1]
        label_items = items[1:]

        pad_len     = self.seq_len - len(input_items)
        item_id_seq = [0] * pad_len + input_items
        label_seq   = [0] * pad_len + label_items

        genre_seq = torch.stack([
            self.get_genre_vec(i) for i in item_id_seq
        ])  # (seq_len, num_genres)

        return {
            "item_id_seq": torch.tensor(item_id_seq, dtype=torch.long),
            "genre_seq":   genre_seq,
            "label_seq":   torch.tensor(label_seq,   dtype=torch.long),
        }
