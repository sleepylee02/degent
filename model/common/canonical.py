from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset
from tqdm import tqdm

from model.common.dataset import parse_ts


@dataclass(frozen=True)
class CanonicalEvent:
    user_id: int
    event_idx: int
    movie_id: int
    item_idx: int
    rated_at_iso: str
    rated_at_ts: float


@dataclass
class CanonicalLoadStats:
    users_seen: int = 0
    filtered_users: int = 0
    kept_users: int = 0
    total_positive_events: int = 0
    kept_events: int = 0
    skipped_unknown_items: int = 0
    skipped_unknown_item_users: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def build_canonical_item_window(events: list[CanonicalEvent], event_position: int, seq_len: int) -> tuple[list[int], int, int]:
    context_start_position = max(0, event_position - seq_len + 1)
    context = events[context_start_position : event_position + 1]
    return (
        [event.item_idx for event in context],
        context[0].event_idx,
        len(context),
    )


def load_canonical_event_sequences(
    jsonl_path: Path,
    item2idx: dict[int, int],
    *,
    min_interactions: int = 1000,
    min_activity_days: int = 30,
    limit_users: int | None = None,
) -> tuple[dict[int, list[CanonicalEvent]], CanonicalLoadStats]:
    stats = CanonicalLoadStats()
    user_events: dict[int, list[CanonicalEvent]] = {}

    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in tqdm(handle, desc="load canonical users"):
            stats.users_seen += 1
            entry = json.loads(line)
            user_id = int(entry["userId"])
            ratings = entry["ratings"]

            first_ts = parse_ts(entry["firstRatedAt"])
            last_ts = parse_ts(entry["lastRatedAt"])
            activity_days = (last_ts - first_ts) / 86400
            if activity_days < min_activity_days:
                continue

            rating_values = np.array([r["rating"] for r in ratings])
            z_scores = (rating_values - rating_values.mean()) / (rating_values.std() + 1e-8)

            positives = [
                {
                    "movie_id": int(rating["movieId"]),
                    "rated_at_iso": str(rating["ratedAt"]),
                    "rated_at_ts": parse_ts(str(rating["ratedAt"])),
                }
                for rating, z_score in zip(ratings, z_scores)
                if z_score > 0
            ]
            positives.sort(key=lambda item: (item["rated_at_ts"], item["movie_id"]))

            if len(positives) < min_interactions:
                continue

            stats.filtered_users += 1
            unknown_items_for_user = 0
            known_events: list[CanonicalEvent] = []

            for event_idx, event in enumerate(positives):
                stats.total_positive_events += 1
                movie_id = int(event["movie_id"])
                item_idx = item2idx.get(movie_id)
                if item_idx is None:
                    stats.skipped_unknown_items += 1
                    unknown_items_for_user += 1
                    continue

                known_events.append(
                    CanonicalEvent(
                        user_id=user_id,
                        event_idx=event_idx,
                        movie_id=movie_id,
                        item_idx=int(item_idx),
                        rated_at_iso=str(event["rated_at_iso"]),
                        rated_at_ts=float(event["rated_at_ts"]),
                    )
                )

            if unknown_items_for_user > 0:
                stats.skipped_unknown_item_users += 1

            if not known_events:
                continue

            user_events[user_id] = known_events
            stats.kept_users += 1
            stats.kept_events += len(known_events)

            if limit_users is not None and stats.kept_users >= limit_users:
                break

    return user_events, stats


def validate_unique_user_events(user_ids: np.ndarray, event_idx: np.ndarray) -> bool:
    if len(user_ids) != len(event_idx):
        return False
    pairs = set(zip(user_ids.tolist(), event_idx.tolist()))
    return len(pairs) == len(user_ids)


class CanonicalEventDataset(Dataset):
    def __init__(
        self,
        user_events: dict[int, list[CanonicalEvent]],
        genre_map: dict[int, list[int]],
        num_genres: int,
        *,
        seq_len: int = 100,
    ):
        self.user_events = user_events
        self.genre_map = genre_map
        self.num_genres = num_genres
        self.seq_len = seq_len
        self.index = [
            (user_id, event_position)
            for user_id, events in user_events.items()
            for event_position in range(len(events))
        ]

    def __len__(self) -> int:
        return len(self.index)

    def get_genre_vec(self, item_id: int) -> torch.Tensor:
        vec = torch.zeros(self.num_genres)
        for genre_idx in self.genre_map.get(item_id, []):
            vec[genre_idx] = 1.0
        return vec

    def __getitem__(self, idx: int) -> dict[str, object]:
        user_id, event_position = self.index[idx]
        events = self.user_events[user_id]
        event = events[event_position]
        item_ids, context_start_idx, history_len = build_canonical_item_window(
            events,
            event_position,
            self.seq_len,
        )

        pad_len = self.seq_len - len(item_ids)
        # Right-padding keeps the canonical event at the last non-padding position.
        # The extractor selects it with SASRecCL.get_last_hidden().
        item_id_seq = item_ids + [0] * pad_len
        genre_seq = torch.stack([self.get_genre_vec(item_id) for item_id in item_id_seq])

        return {
            "item_id_seq": torch.tensor(item_id_seq, dtype=torch.long),
            "genre_seq": genre_seq,
            "user_id": torch.tensor(user_id, dtype=torch.long),
            "event_idx": torch.tensor(event.event_idx, dtype=torch.long),
            "movie_id": torch.tensor(event.movie_id, dtype=torch.long),
            "rated_at_ts": torch.tensor(event.rated_at_ts, dtype=torch.float64),
            "rated_at_iso": event.rated_at_iso,
            "history_len": torch.tensor(history_len, dtype=torch.long),
            "context_start_idx": torch.tensor(context_start_idx, dtype=torch.long),
        }
