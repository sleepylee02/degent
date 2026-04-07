import torch
import torch.nn as nn
import torch.nn.functional as F


# =====================
# Augmentation
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
    mask = (aug_item != 0) & (rand < mask_prob)

    aug_item[mask]  = 0
    aug_genre[mask] = 0

    return aug_item, aug_genre


# =====================
# Contrastive Loss
# =====================

def contrastive_loss(h1, h2, temperature=0.1):
    """
    h1, h2: (B, d_model) - augmentation으로 만든 positive pair
    InfoNCE loss
    """
    B  = h1.size(0)
    h1 = F.normalize(h1, dim=-1)
    h2 = F.normalize(h2, dim=-1)

    h   = torch.cat([h1, h2], dim=0)
    sim = torch.matmul(h, h.T) / temperature

    labels = torch.cat([
        torch.arange(B, 2 * B, device=h.device),
        torch.arange(0, B,     device=h.device)
    ])

    mask = torch.eye(2 * B, device=h.device).bool()
    sim.masked_fill_(mask, float('-inf'))

    return F.cross_entropy(sim, labels)


# =====================
# 모델
# =====================

class SASRecCL(nn.Module):
    def __init__(self, num_items, num_genres, d_model=128,
                 num_heads=2, num_layers=2, dropout=0.2, max_len=100):
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

        item_e  = self.item_emb(item_id_seq)
        genre_e = self.genre_emb(genre_seq.float())

        x = self.input_proj(torch.cat([item_e, genre_e], dim=-1))

        pos = torch.arange(L, device=x.device).unsqueeze(0)
        x   = x + self.pos_emb(pos)

        pad_mask = (item_id_seq == 0)

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
