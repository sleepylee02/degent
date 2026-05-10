from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from model.common.canonical import CanonicalEvent
from model.common.dataset import parse_ts
from model.common.runtime import local_timestamp


STATE_VERSION = "online_user_state.v1"
POSITIVE_POLICY_NAME = "observed_user_zscore_v1"


@dataclass(frozen=True)
class PositivePolicy:
    min_ratings_for_zscore: int = 3
    z_threshold: float = 0.0
    optimistic_cold_start: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": POSITIVE_POLICY_NAME,
            "minRatingsForZscore": self.min_ratings_for_zscore,
            "zThreshold": self.z_threshold,
            "optimisticColdStart": self.optimistic_cold_start,
        }

    @classmethod
    def from_dict(cls, item: dict[str, Any] | None) -> "PositivePolicy":
        if not item:
            return cls()
        return cls(
            min_ratings_for_zscore=int(item.get("minRatingsForZscore", 3)),
            z_threshold=float(item.get("zThreshold", 0.0)),
            optimistic_cold_start=bool(item.get("optimisticColdStart", True)),
        )


@dataclass(frozen=True)
class RawRatingEvent:
    raw_event_id: int
    user_id: int
    movie_id: int
    rating: float
    rated_at_iso: str
    rated_at_ts: float
    ingested_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rawEventId": self.raw_event_id,
            "userId": self.user_id,
            "movieId": self.movie_id,
            "rating": self.rating,
            "ratedAt": self.rated_at_iso,
            "ratedAtTs": self.rated_at_ts,
            "ingestedAt": self.ingested_at,
        }

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "RawRatingEvent":
        rated_at_iso = str(item.get("ratedAt", item.get("rated_at_iso")))
        return cls(
            raw_event_id=int(item.get("rawEventId", item.get("raw_event_id"))),
            user_id=int(item.get("userId", item.get("user_id"))),
            movie_id=int(item.get("movieId", item.get("movie_id"))),
            rating=float(item["rating"]),
            rated_at_iso=rated_at_iso,
            rated_at_ts=float(item.get("ratedAtTs", item.get("rated_at_ts", parse_ts(rated_at_iso)))),
            ingested_at=str(item.get("ingestedAt", item.get("ingested_at", local_timestamp()))),
        )


@dataclass(frozen=True)
class PositiveEvent:
    raw_event_id: int
    user_id: int
    event_idx: int
    movie_id: int
    rating: float
    item_idx: int | None
    rated_at_iso: str
    rated_at_ts: float
    z_score: float | None
    status: str
    positive_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rawEventId": self.raw_event_id,
            "userId": self.user_id,
            "eventIdx": self.event_idx,
            "movieId": self.movie_id,
            "rating": self.rating,
            "itemIdx": self.item_idx,
            "ratedAt": self.rated_at_iso,
            "ratedAtTs": self.rated_at_ts,
            "zScore": self.z_score,
            "status": self.status,
            "positiveReason": self.positive_reason,
        }

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "PositiveEvent":
        item_idx = item.get("itemIdx", item.get("item_idx"))
        z_score = item.get("zScore", item.get("z_score"))
        return cls(
            raw_event_id=int(item.get("rawEventId", item.get("raw_event_id"))),
            user_id=int(item.get("userId", item.get("user_id"))),
            event_idx=int(item.get("eventIdx", item.get("event_idx"))),
            movie_id=int(item.get("movieId", item.get("movie_id"))),
            rating=float(item["rating"]),
            item_idx=None if item_idx is None else int(item_idx),
            rated_at_iso=str(item.get("ratedAt", item.get("rated_at_iso"))),
            rated_at_ts=float(item.get("ratedAtTs", item.get("rated_at_ts"))),
            z_score=None if z_score is None else float(z_score),
            status=str(item["status"]),
            positive_reason=str(item.get("positiveReason", item.get("positive_reason", "unknown"))),
        )


@dataclass
class OnlineUserState:
    user_id: int
    seq_len: int
    positive_policy: PositivePolicy
    next_raw_event_id: int = 0
    raw_events: list[RawRatingEvent] | None = None
    positive_events: list[PositiveEvent] | None = None
    stats: dict[str, Any] | None = None
    updated_at: str | None = None
    version: str = STATE_VERSION

    def __post_init__(self) -> None:
        if self.raw_events is None:
            self.raw_events = []
        if self.positive_events is None:
            self.positive_events = []
        if self.stats is None:
            self.stats = {}
        if self.updated_at is None:
            self.updated_at = local_timestamp()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "userId": self.user_id,
            "seqLen": self.seq_len,
            "nextRawEventId": self.next_raw_event_id,
            "positivePolicy": self.positive_policy.to_dict(),
            "rawEvents": [event.to_dict() for event in self.raw_events or []],
            "positiveEvents": [event.to_dict() for event in self.positive_events or []],
            "stats": self.stats or {},
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "OnlineUserState":
        return cls(
            version=str(item.get("version", STATE_VERSION)),
            user_id=int(item.get("userId", item.get("user_id"))),
            seq_len=int(item.get("seqLen", item.get("seq_len"))),
            next_raw_event_id=int(item.get("nextRawEventId", item.get("next_raw_event_id", 0))),
            positive_policy=PositivePolicy.from_dict(item.get("positivePolicy")),
            raw_events=[RawRatingEvent.from_dict(event) for event in item.get("rawEvents", [])],
            positive_events=[PositiveEvent.from_dict(event) for event in item.get("positiveEvents", [])],
            stats=dict(item.get("stats", {})),
            updated_at=str(item.get("updatedAt", local_timestamp())),
        )


def sort_raw_events(events: list[RawRatingEvent]) -> list[RawRatingEvent]:
    return sorted(events, key=lambda event: (event.rated_at_ts, event.movie_id, event.raw_event_id))


def load_user_state(path: Path) -> OnlineUserState | None:
    if not path.exists():
        return None

    import json

    return OnlineUserState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_user_state(state: OnlineUserState, path: Path) -> Path:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def state_path_for_user(state_dir: Path, user_id: int) -> Path:
    return state_dir / f"{int(user_id)}.json"


def make_empty_state(user_id: int, *, seq_len: int, positive_policy: PositivePolicy) -> OnlineUserState:
    return OnlineUserState(user_id=int(user_id), seq_len=int(seq_len), positive_policy=positive_policy)


def append_rating_event(
    state: OnlineUserState,
    *,
    movie_id: int,
    rating: float,
    rated_at_iso: str,
    item2idx: dict[int, int],
    raw_event_id: int | None = None,
    ingested_at: str | None = None,
) -> RawRatingEvent:
    raw_event_id = state.next_raw_event_id if raw_event_id is None else int(raw_event_id)
    if any(event.raw_event_id == raw_event_id for event in state.raw_events or []):
        raise ValueError(f"Duplicate rawEventId for user {state.user_id}: {raw_event_id}")

    event = RawRatingEvent(
        raw_event_id=raw_event_id,
        user_id=state.user_id,
        movie_id=int(movie_id),
        rating=float(rating),
        rated_at_iso=str(rated_at_iso),
        rated_at_ts=parse_ts(str(rated_at_iso)),
        ingested_at=ingested_at or local_timestamp(),
    )
    state.raw_events = sort_raw_events([*(state.raw_events or []), event])
    state.next_raw_event_id = max(state.next_raw_event_id, raw_event_id + 1)
    rebuild_positive_projection(state, item2idx)
    return event


def rebuild_positive_projection(state: OnlineUserState, item2idx: dict[int, int]) -> None:
    raw_events = sort_raw_events(state.raw_events or [])
    ratings = np.array([event.rating for event in raw_events], dtype=np.float64)
    policy = state.positive_policy

    if len(raw_events) == 0:
        positive_mask = np.array([], dtype=bool)
        z_scores: list[float | None] = []
        reason = "empty"
        mean_rating = None
        std_rating = None
    else:
        mean_rating = float(ratings.mean())
        std_rating = float(ratings.std())
        if len(raw_events) < policy.min_ratings_for_zscore and policy.optimistic_cold_start:
            positive_mask = np.ones(len(raw_events), dtype=bool)
            z_scores = [None] * len(raw_events)
            reason = "cold_start"
        elif std_rating <= 1e-8 and policy.optimistic_cold_start:
            positive_mask = np.ones(len(raw_events), dtype=bool)
            z_scores = [None] * len(raw_events)
            reason = "zero_std"
        else:
            z_array = (ratings - mean_rating) / (std_rating + 1e-8)
            positive_mask = z_array > policy.z_threshold
            z_scores = [float(value) for value in z_array]
            reason = "zscore"

    positive_events: list[PositiveEvent] = []
    for event, is_positive, z_score in zip(raw_events, positive_mask.tolist(), z_scores):
        if not is_positive:
            continue

        item_idx = item2idx.get(event.movie_id)
        status = "active" if item_idx is not None else "skipped_unknown"
        positive_events.append(
            PositiveEvent(
                raw_event_id=event.raw_event_id,
                user_id=state.user_id,
                event_idx=len(positive_events),
                movie_id=event.movie_id,
                rating=event.rating,
                item_idx=None if item_idx is None else int(item_idx),
                rated_at_iso=event.rated_at_iso,
                rated_at_ts=event.rated_at_ts,
                z_score=z_score,
                status=status,
                positive_reason=reason,
            )
        )

    active_events = [event for event in positive_events if event.status == "active"]
    skipped_unknown = [event for event in positive_events if event.status == "skipped_unknown"]
    state.raw_events = raw_events
    state.positive_events = positive_events
    state.stats = {
        "rawEventCount": len(raw_events),
        "positiveEventCount": len(positive_events),
        "activeEventCount": len(active_events),
        "droppedEventCount": len(raw_events) - len(positive_events),
        "skippedUnknownItems": len(skipped_unknown),
        "ratingMean": mean_rating,
        "ratingStd": std_rating,
        "positiveReason": reason,
        "positivePolicy": policy.to_dict(),
    }
    state.updated_at = local_timestamp()


def active_positive_events(state: OnlineUserState) -> list[PositiveEvent]:
    return [event for event in state.positive_events or [] if event.status == "active" and event.item_idx is not None]


def canonical_events_from_state(state: OnlineUserState) -> list[CanonicalEvent]:
    return [
        CanonicalEvent(
            user_id=state.user_id,
            event_idx=event.event_idx,
            movie_id=event.movie_id,
            item_idx=int(event.item_idx),
            rated_at_iso=event.rated_at_iso,
            rated_at_ts=event.rated_at_ts,
        )
        for event in active_positive_events(state)
    ]


def build_state_from_rating_history(
    *,
    user_id: int,
    ratings: list[dict[str, Any]],
    seq_len: int,
    item2idx: dict[int, int],
    positive_policy: PositivePolicy,
) -> OnlineUserState:
    state = make_empty_state(user_id, seq_len=seq_len, positive_policy=positive_policy)
    sorted_ratings = sorted(
        ratings,
        key=lambda item: (parse_ts(str(item["ratedAt"])), int(item["movieId"])),
    )
    for raw_event_id, rating in enumerate(sorted_ratings):
        state.raw_events.append(
            RawRatingEvent(
                raw_event_id=raw_event_id,
                user_id=int(user_id),
                movie_id=int(rating["movieId"]),
                rating=float(rating["rating"]),
                rated_at_iso=str(rating["ratedAt"]),
                rated_at_ts=parse_ts(str(rating["ratedAt"])),
                ingested_at=local_timestamp(),
            )
        )
    state.next_raw_event_id = len(state.raw_events)
    rebuild_positive_projection(state, item2idx)
    return state
