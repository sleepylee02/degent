import json
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from datetime import datetime
from collections import defaultdict

# =====================
# 1. 전처리 유틸
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


def build_user_sequences(jsonl_path, min_interactions=10):
    """
    ratings_drop_processed.jsonl 로드
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

            if len(ratings) < min_interactions:
                continue

            rating_values = np.array([r['rating'] for r in ratings])
            z_scores = (rating_values - rating_values.mean()) / (rating_values.std() + 1e-8)

            positive = [
                (r['movieId'], parse_ts(r['ratedAt']))
                for r, z in zip(ratings, z_scores)
                if z > 0
            ]

            if len(positive) < min_interactions:
                continue

            positive.sort(key=lambda x: x[1])  # 이미 정렬돼 있지만 명시적으로
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
# 2. 데이터셋
# =====================

class MovieLensDataset(Dataset):
    def __init__(self, user_sequences, genre_map, num_genres, seq_len=50):
        """
        user_sequences: {userId: [movieId, ...]}  (item2idx 재매핑 완료된 상태)
        genre_map:      {movieId(remapped): [genre_idx, ...]}
        """
        self.data       = []
        self.genre_map  = genre_map
        self.num_genres = num_genres
        self.seq_len    = seq_len

        for user_id, items in user_sequences.items():
            if len(items) < 3:
                continue
            self.data.append(items)

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
        item_id_seq = [0] * pad_len + input_items   # 0 = padding
        label_seq   = [0] * pad_len + label_items

        genre_seq = torch.stack([
            self.get_genre_vec(i) for i in item_id_seq
        ])  # (seq_len, num_genres)

        return {
            "item_id_seq": torch.tensor(item_id_seq, dtype=torch.long),
            "genre_seq":   genre_seq,
            "label_seq":   torch.tensor(label_seq,   dtype=torch.long),
        }


# =====================
# 3. Augmentation
# =====================

def augment_sequence(item_id_seq, genre_seq, mask_prob=0.2):
    """
    아이템 마스킹 + 마스킹된 위치의 장르도 함께 0으로
    item_id_seq: (B, L)
    genre_seq:   (B, L, num_genres)
    """
    aug_item  = item_id_seq.clone()
    aug_genre = genre_seq.clone()

    rand = torch.rand_like(aug_item.float())
    mask = (aug_item != 0) & (rand < mask_prob)  # padding 제외하고 마스킹

    aug_item[mask]  = 0
    aug_genre[mask] = 0

    return aug_item, aug_genre


# =====================
# 4. 모델
# =====================

class SASRecCL(nn.Module):
    def __init__(self, num_items, num_genres, d_model=128,
                 num_heads=2, num_layers=2, dropout=0.2, max_len=50):
        super().__init__()

        # d_model로 통일 → weight tying 가능
        self.item_emb   = nn.Embedding(num_items + 1, d_model, padding_idx=0)
        self.genre_emb  = nn.Linear(num_genres, d_model)
        self.input_proj = nn.Linear(d_model * 2, d_model)
        self.pos_emb    = nn.Embedding(max_len, d_model)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=num_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.dropout     = nn.Dropout(dropout)
        self.layer_norm  = nn.LayerNorm(d_model)
        self.d_model     = d_model
        self.num_items   = num_items

    def encode(self, item_id_seq, genre_seq):
        """
        item_id_seq: (B, L)
        genre_seq:   (B, L, num_genres)
        returns:     (B, L, d_model)
        """
        B, L = item_id_seq.shape

        item_e  = self.item_emb(item_id_seq)         # (B, L, d_model)
        genre_e = self.genre_emb(genre_seq.float())  # (B, L, d_model)

        x = self.input_proj(torch.cat([item_e, genre_e], dim=-1))  # (B, L, d_model)

        pos = torch.arange(L, device=x.device).unsqueeze(0)
        x   = x + self.pos_emb(pos)

        # padding mask
        pad_mask = (item_id_seq == 0)  # (B, L)

        # causal mask: position t는 1..t까지만 참조
        causal_mask = torch.triu(
            torch.ones(L, L, device=x.device, dtype=torch.bool),
            diagonal=1
        )

        x = self.dropout(x)
        x = self.transformer(
            x,
            mask=causal_mask,
            src_key_padding_mask=pad_mask
        )
        x = self.layer_norm(x)
        return x  # (B, L, d_model)

    def get_last_hidden(self, item_id_seq, genre_seq):
        """실제 마지막 non-padding 위치의 ht 추출"""
        h       = self.encode(item_id_seq, genre_seq)
        lengths = (item_id_seq != 0).sum(dim=1) - 1
        last_h  = h[torch.arange(h.size(0), device=h.device), lengths]
        return last_h  # (B, d_model)

    def score(self, ht):
        """weight tying으로 scoring"""
        return ht @ self.item_emb.weight.T  # (..., num_items+1)

    def forward(self, item_id_seq, genre_seq):
        return self.encode(item_id_seq, genre_seq)


# =====================
# 5. Contrastive Loss
# =====================

def contrastive_loss(h1, h2, temperature=0.1):
    """
    h1, h2: (B, d_model) - augmentation으로 만든 positive pair
    InfoNCE loss
    """
    B  = h1.size(0)
    h1 = F.normalize(h1, dim=-1)
    h2 = F.normalize(h2, dim=-1)

    h   = torch.cat([h1, h2], dim=0)           # (2B, d_model)
    sim = torch.matmul(h, h.T) / temperature   # (2B, 2B)

    labels = torch.cat([
        torch.arange(B, 2 * B, device=h.device),
        torch.arange(0, B,     device=h.device)
    ])

    mask = torch.eye(2 * B, device=h.device).bool()
    sim.masked_fill_(mask, float('-inf'))

    return F.cross_entropy(sim, labels)


# =====================
# 6. Trainer
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

        for batch in dataloader:
            item_id_seq = batch["item_id_seq"].to(self.device)
            genre_seq   = batch["genre_seq"].to(self.device)
            label_seq   = batch["label_seq"].to(self.device)

            # ── CE Loss ──
            ht     = self.model(item_id_seq, genre_seq)       # (B, L, d_model)
            logits = self.model.score(ht)                     # (B, L, num_items+1)

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

        return total_loss / len(dataloader)

    @torch.no_grad()
    def evaluate(self, dataloader, k=10):
        """Recall@K, NDCG@K"""
        self.model.eval()
        recalls, ndcgs = [], []

        for batch in dataloader:
            item_id_seq = batch["item_id_seq"].to(self.device)
            genre_seq   = batch["genre_seq"].to(self.device)
            label_seq   = batch["label_seq"].to(self.device)

            ht     = self.model(item_id_seq, genre_seq)
            logits = self.model.score(ht)                     # (B, L, num_items+1)

            lengths     = (item_id_seq != 0).sum(dim=1) - 1
            last_logits = logits[torch.arange(logits.size(0)), lengths]   # (B, num_items+1)
            last_labels = label_seq[torch.arange(label_seq.size(0)), lengths]  # (B,)

            topk = last_logits.topk(k, dim=-1).indices        # (B, K)

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

    @torch.no_grad()
    def extract_embeddings(self, dataloader):
        """UMAP → HDBSCAN에 넣을 ht 추출"""
        self.model.eval()
        all_embeddings = []

        for batch in dataloader:
            item_id_seq = batch["item_id_seq"].to(self.device)
            genre_seq   = batch["genre_seq"].to(self.device)
            last_h      = self.model.get_last_hidden(item_id_seq, genre_seq)
            all_embeddings.append(last_h.cpu().numpy())

        return np.concatenate(all_embeddings, axis=0)  # (N, d_model)


# =====================
# 7. 실행
# =====================

if __name__ == "__main__":
    # 데이터 로드
    movies = pd.read_csv('data/movies_processed_drop.csv')
    genre_map, all_genres = build_genre_map(movies)
    num_genres            = len(all_genres)

    user_sequences = build_user_sequences('data/ratings_drop_processed.jsonl')

    # item id 재매핑 (0: padding)
    all_items = sorted(set(i for seq in user_sequences.values() for i, _ in seq))
    item2idx  = {item: idx + 1 for idx, item in enumerate(all_items)}
    num_items = len(item2idx)

    genre_map_idx = {item2idx[k]: v for k, v in genre_map.items() if k in item2idx}

    # 시간 기준 global split
    train_seq, val_seq, test_seq = temporal_split(user_sequences)

    def remap(sequences):
        return {
            u: [item2idx[i] for i in seq if i in item2idx]
            for u, seq in sequences.items()
        }

    train_seq_idx = remap(train_seq)
    val_seq_idx   = remap(val_seq)

    # Dataset / DataLoader
    train_dataset = MovieLensDataset(train_seq_idx, genre_map_idx, num_genres, seq_len=50)
    val_dataset   = MovieLensDataset(val_seq_idx,   genre_map_idx, num_genres, seq_len=50)
    train_loader  = DataLoader(train_dataset, batch_size=256, shuffle=True,  num_workers=4)
    val_loader    = DataLoader(val_dataset,   batch_size=256, shuffle=False, num_workers=4)

    # 모델 초기화
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model  = SASRecCL(
        num_items  = num_items,
        num_genres = num_genres,
        d_model    = 128,
        num_heads  = 2,
        num_layers = 2,
        dropout    = 0.2,
        max_len    = 50
    )

    trainer = Trainer(model, lr=1e-3, cl_lambda=0.1, device=device)

    # 학습
    for epoch in range(20):
        loss = trainer.train_epoch(train_loader)
        print(f"Epoch {epoch+1:02d} | Loss: {loss:.4f}")

        if (epoch + 1) % 5 == 0:
            metrics = trainer.evaluate(val_loader, k=10)
            print(f"  Val → {metrics}")

    # ht 추출 → UMAP → HDBSCAN
    embeddings = trainer.extract_embeddings(train_loader)
    print(f"Embeddings shape: {embeddings.shape}")  # (N, 128)

    # 이후 파이프라인
    # import umap, hdbscan
    # reducer   = umap.UMAP(n_components=3, random_state=42)
    # z         = reducer.fit_transform(embeddings)
    # clusterer = hdbscan.HDBSCAN(min_cluster_size=10)
    # labels    = clusterer.fit_predict(z)