#!/usr/bin/env python3
"""2020-area temporal cutoff deep dive using the current model pipeline rules."""

from __future__ import annotations

import argparse
import bisect
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any

import matplotlib

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
OUTPUT_DIR = SCRIPT_DIR / "outputs"
MPLCONFIG_DIR = OUTPUT_DIR / ".mplconfig"

os.environ.setdefault("MPLCONFIGDIR", str(MPLCONFIG_DIR))
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import polars as pl


RATED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_RATINGS_SEQUENCE_PATH = REPO_ROOT / "data" / "ratings_drop_processed.jsonl"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR
DEFAULT_CUTOFFS = ",".join(
    [
        "pre_2020=2019-12-31T23:59:59Z",
        "mid_2020=2020-06-30T23:59:59Z",
        "q90_2020=2020-10-29T23:59:59Z",
        "end_2020=2020-12-31T23:59:59Z",
    ]
)


@dataclass(frozen=True)
class Cutoff:
    candidate_id: str
    cutoff: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ratings-sequence", type=Path, default=DEFAULT_RATINGS_SEQUENCE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cutoffs", default=DEFAULT_CUTOFFS, help="Comma list of id=UTC_ISO cutoff values.")
    parser.add_argument("--seq-len", type=int, default=100)
    parser.add_argument("--stride", type=int, default=50)
    parser.add_argument("--train-min-interactions", type=int, default=200)
    parser.add_argument("--canonical-min-interactions", type=int, default=1000)
    parser.add_argument("--min-activity-days", type=int, default=30)
    parser.add_argument("--positive-rating-threshold", type=float, default=4.0)
    parser.add_argument("--refit-min-events", type=int, default=20)
    parser.add_argument("--assign-trigger-count", type=int, default=50)
    parser.add_argument("--outlier-trigger-count", type=int, default=10)
    return parser.parse_args()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_cutoffs(value: str) -> list[Cutoff]:
    cutoffs: list[Cutoff] = []
    for item in value.split(","):
        if not item.strip():
            continue
        candidate_id, cutoff = item.split("=", 1)
        cutoffs.append(Cutoff(candidate_id.strip(), cutoff.strip()))
    return sorted(cutoffs, key=lambda item: item.cutoff)


def parse_iso_utc(value: str) -> datetime:
    return datetime.strptime(value, RATED_AT_FORMAT).replace(tzinfo=timezone.utc)


def format_int(value: int | float) -> str:
    return f"{int(round(value)):,}"


def format_float(value: float, digits: int = 2) -> str:
    if math.isnan(value):
        return "n/a"
    return f"{value:.{digits}f}"


def format_pct(value: float, digits: int = 2) -> str:
    if math.isnan(value):
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def percent(numerator: int | float, denominator: int | float) -> str:
    if denominator == 0:
        return "0.00%"
    return format_pct(float(numerator) / float(denominator))


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, divider, *body])


def stats(values: list[int] | np.ndarray) -> dict[str, float]:
    arr = np.array(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return {key: float("nan") for key in ["p50", "p90", "p95", "p99", "max", "mean"]}
    return {
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
    }


def batch_positive_mask(ratings: np.ndarray) -> np.ndarray:
    if len(ratings) == 0:
        return np.array([], dtype=bool)
    z_scores = (ratings - ratings.mean()) / (ratings.std() + 1e-8)
    return z_scores > 0


def online_positive_mask(ratings: np.ndarray, *, min_ratings_for_zscore: int = 3) -> np.ndarray:
    if len(ratings) == 0:
        return np.array([], dtype=bool)
    if len(ratings) < min_ratings_for_zscore:
        return np.ones(len(ratings), dtype=bool)
    if ratings.std() <= 1e-8:
        return np.ones(len(ratings), dtype=bool)
    z_scores = (ratings - ratings.mean()) / (ratings.std() + 1e-8)
    return z_scores > 0


def activity_days(rated_ats: list[str]) -> float:
    if len(rated_ats) < 2:
        return 0.0
    return (parse_iso_utc(rated_ats[-1]) - parse_iso_utc(rated_ats[0])).total_seconds() / 86400.0


def dataset_window_samples(sequence_len: int, *, seq_len: int, stride: int) -> int:
    if sequence_len < 3:
        return 0
    if sequence_len <= seq_len + 1:
        return 1
    return ((sequence_len - seq_len - 1) // stride) + 1


def empty_metric(cutoff: Cutoff) -> dict[str, Any]:
    return {
        "candidate_id": cutoff.candidate_id,
        "cutoff": cutoff.cutoff,
        "total_users": 0,
        "pre_users": 0,
        "post_users": 0,
        "pre_raw_events": 0,
        "post_raw_events": 0,
        "train_eligible_users": 0,
        "canonical_eligible_users": 0,
        "pre_positive_events_train_users": 0,
        "pre_training_window_samples": 0,
        "item_vocab_size": 0,
        "post_events_canonical_seed_user": 0,
        "post_events_train_only_user": 0,
        "post_events_existing_untrained_user": 0,
        "post_events_new_user": 0,
        "post_known_item_events": 0,
        "post_unknown_item_events": 0,
        "post_positive_proxy_known_events": 0,
        "post_positive_proxy_unknown_events": 0,
        "online_recompute_rows_est": 0.0,
        "online_recompute_rows_per_event_est": 0.0,
        "refit_candidate_seeded_assign_trigger_users": 0,
        "refit_candidate_no_seed_min_events_users": 0,
        "refit_cluster_rows_est": 0,
    }


def first_pass(
    path: Path,
    cutoffs: list[Cutoff],
    *,
    train_min_interactions: int,
    canonical_min_interactions: int,
    min_activity_days: int,
    seq_len: int,
    stride: int,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, set[int]],
    dict[str, set[int]],
    dict[str, set[int]],
    dict[str, dict[int, int]],
    dict[str, list[int]],
]:
    metrics = {cutoff.candidate_id: empty_metric(cutoff) for cutoff in cutoffs}
    item_vocab = {cutoff.candidate_id: set() for cutoff in cutoffs}
    train_users = {cutoff.candidate_id: set() for cutoff in cutoffs}
    canonical_users = {cutoff.candidate_id: set() for cutoff in cutoffs}
    pre_counts = {cutoff.candidate_id: {} for cutoff in cutoffs}
    train_sequence_lengths = {cutoff.candidate_id: [] for cutoff in cutoffs}

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = json.loads(line)
            user_id = int(entry["userId"])
            ratings = entry["ratings"]
            rated_ats = [str(item["ratedAt"]) for item in ratings]
            movie_ids = np.array([int(item["movieId"]) for item in ratings], dtype=np.int64)
            rating_values = np.array([float(item["rating"]) for item in ratings], dtype=np.float64)
            total_count = len(ratings)

            for cutoff in cutoffs:
                metric = metrics[cutoff.candidate_id]
                metric["total_users"] += 1
                idx = bisect.bisect_right(rated_ats, cutoff.cutoff)
                pre_counts[cutoff.candidate_id][user_id] = idx
                post_count = total_count - idx
                metric["pre_raw_events"] += idx
                metric["post_raw_events"] += post_count
                if idx > 0:
                    metric["pre_users"] += 1
                if post_count > 0:
                    metric["post_users"] += 1

                pre_ratings = rating_values[:idx]
                pre_positive = batch_positive_mask(pre_ratings)
                pre_positive_count = int(pre_positive.sum())
                span_days = activity_days(rated_ats[:idx])
                if span_days < min_activity_days or pre_positive_count < train_min_interactions:
                    continue

                train_users[cutoff.candidate_id].add(user_id)
                train_sequence_lengths[cutoff.candidate_id].append(pre_positive_count)
                metric["train_eligible_users"] += 1
                metric["pre_positive_events_train_users"] += pre_positive_count
                metric["pre_training_window_samples"] += dataset_window_samples(
                    pre_positive_count,
                    seq_len=seq_len,
                    stride=stride,
                )
                item_vocab[cutoff.candidate_id].update(movie_ids[:idx][pre_positive].tolist())
                if pre_positive_count >= canonical_min_interactions:
                    canonical_users[cutoff.candidate_id].add(user_id)
                    metric["canonical_eligible_users"] += 1

    for cutoff in cutoffs:
        metrics[cutoff.candidate_id]["item_vocab_size"] = len(item_vocab[cutoff.candidate_id])

    return metrics, item_vocab, train_users, canonical_users, pre_counts, train_sequence_lengths


def second_pass(
    path: Path,
    cutoffs: list[Cutoff],
    metrics: dict[str, dict[str, Any]],
    item_vocab: dict[str, set[int]],
    train_users: dict[str, set[int]],
    canonical_users: dict[str, set[int]],
    pre_counts: dict[str, dict[int, int]],
    *,
    positive_rating_threshold: float,
    refit_min_events: int,
    assign_trigger_count: int,
) -> tuple[dict[str, list[int]], dict[str, list[int]], dict[str, list[int]], dict[str, Counter], dict[str, Counter]]:
    post_user_event_counts = {cutoff.candidate_id: [] for cutoff in cutoffs}
    post_known_positive_counts = {cutoff.candidate_id: [] for cutoff in cutoffs}
    final_active_known_lengths = {cutoff.candidate_id: [] for cutoff in cutoffs}
    daily_counts = {cutoff.candidate_id: Counter() for cutoff in cutoffs}
    hourly_counts = {cutoff.candidate_id: Counter() for cutoff in cutoffs}

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = json.loads(line)
            user_id = int(entry["userId"])
            ratings = entry["ratings"]
            rated_ats = [str(item["ratedAt"]) for item in ratings]
            movie_ids = np.array([int(item["movieId"]) for item in ratings], dtype=np.int64)
            rating_values = np.array([float(item["rating"]) for item in ratings], dtype=np.float64)
            total_count = len(ratings)

            for cutoff in cutoffs:
                cid = cutoff.candidate_id
                metric = metrics[cid]
                idx = pre_counts[cid][user_id]
                post_count = total_count - idx
                if post_count <= 0:
                    continue

                if user_id in canonical_users[cid]:
                    category = "canonical_seed_user"
                elif user_id in train_users[cid]:
                    category = "train_only_user"
                elif idx > 0:
                    category = "existing_untrained_user"
                else:
                    category = "new_user"
                metric[f"post_events_{category}"] += post_count

                vocab = item_vocab[cid]
                post_movies = movie_ids[idx:]
                post_ratings = rating_values[idx:]
                known_mask = np.fromiter((int(movie_id) in vocab for movie_id in post_movies), dtype=bool)
                positive_proxy_mask = post_ratings >= positive_rating_threshold
                known_events = int(known_mask.sum())
                positive_known = int((known_mask & positive_proxy_mask).sum())
                positive_unknown = int((~known_mask & positive_proxy_mask).sum())
                metric["post_known_item_events"] += known_events
                metric["post_unknown_item_events"] += post_count - known_events
                metric["post_positive_proxy_known_events"] += positive_known
                metric["post_positive_proxy_unknown_events"] += positive_unknown

                pre_online_mask = online_positive_mask(rating_values[:idx])
                final_online_mask = online_positive_mask(rating_values)
                pre_known_mask = np.fromiter((int(movie_id) in vocab for movie_id in movie_ids[:idx]), dtype=bool)
                final_known_mask = np.fromiter((int(movie_id) in vocab for movie_id in movie_ids), dtype=bool)
                pre_active_known = int((pre_known_mask & pre_online_mask).sum()) if idx > 0 else 0
                final_active_known = int((final_known_mask & final_online_mask).sum())
                metric["online_recompute_rows_est"] += post_count * ((pre_active_known + final_active_known) / 2.0)
                final_active_known_lengths[cid].append(final_active_known)
                post_user_event_counts[cid].append(post_count)
                post_known_positive_counts[cid].append(positive_known)

                if category == "canonical_seed_user":
                    if positive_known >= assign_trigger_count:
                        metric["refit_candidate_seeded_assign_trigger_users"] += 1
                        metric["refit_cluster_rows_est"] += final_active_known
                else:
                    if positive_known >= refit_min_events:
                        metric["refit_candidate_no_seed_min_events_users"] += 1
                        metric["refit_cluster_rows_est"] += final_active_known

                for rated_at in rated_ats[idx:]:
                    daily_counts[cid][rated_at[:10]] += 1
                    hourly_counts[cid][rated_at[:13]] += 1

    for cutoff in cutoffs:
        cid = cutoff.candidate_id
        post_events = metrics[cid]["post_raw_events"]
        metrics[cid]["online_recompute_rows_per_event_est"] = (
            metrics[cid]["online_recompute_rows_est"] / post_events if post_events else 0.0
        )

    return post_user_event_counts, post_known_positive_counts, final_active_known_lengths, daily_counts, hourly_counts


def add_distribution_columns(
    rows: list[dict[str, Any]],
    *,
    train_sequence_lengths: dict[str, list[int]],
    post_user_event_counts: dict[str, list[int]],
    post_known_positive_counts: dict[str, list[int]],
    final_active_known_lengths: dict[str, list[int]],
    daily_counts: dict[str, Counter],
    hourly_counts: dict[str, Counter],
) -> None:
    for row in rows:
        cid = row["candidate_id"]
        for prefix, values in [
            ("train_positive_seq_len", train_sequence_lengths[cid]),
            ("post_user_events", post_user_event_counts[cid]),
            ("post_known_positive_proxy", post_known_positive_counts[cid]),
            ("final_active_known_seq_len", final_active_known_lengths[cid]),
            ("post_daily_events", list(daily_counts[cid].values())),
            ("post_hourly_events", list(hourly_counts[cid].values())),
        ]:
            distribution = stats(values)
            for key, value in distribution.items():
                row[f"{prefix}_{key}"] = value

        post_events = row["post_raw_events"]
        row["post_known_item_event_share"] = row["post_known_item_events"] / post_events if post_events else 0.0
        row["post_unknown_item_event_share"] = row["post_unknown_item_events"] / post_events if post_events else 0.0
        row["post_positive_proxy_known_event_share"] = (
            row["post_positive_proxy_known_events"] / post_events if post_events else 0.0
        )
        row["post_events_canonical_seed_user_share"] = (
            row["post_events_canonical_seed_user"] / post_events if post_events else 0.0
        )
        row["post_events_train_only_user_share"] = (
            row["post_events_train_only_user"] / post_events if post_events else 0.0
        )
        row["post_events_existing_untrained_user_share"] = (
            row["post_events_existing_untrained_user"] / post_events if post_events else 0.0
        )
        row["post_events_new_user_share"] = row["post_events_new_user"] / post_events if post_events else 0.0


def plot_stacked_post_categories(path: Path, rows: list[dict[str, Any]]) -> None:
    labels = [row["candidate_id"] for row in rows]
    categories = [
        ("post_events_canonical_seed_user", "canonical seed"),
        ("post_events_train_only_user", "train only"),
        ("post_events_existing_untrained_user", "existing untrained"),
        ("post_events_new_user", "new user"),
    ]
    colors = ["#2A9D8F", "#264653", "#F4A261", "#E76F51"]
    x = np.arange(len(labels))
    bottom = np.zeros(len(labels))
    plt.figure(figsize=(11, 5))
    for (column, label), color in zip(categories, colors):
        values = np.array([row[column] for row in rows], dtype=float) / 1_000_000
        plt.bar(x, values, bottom=bottom, label=label, color=color)
        bottom += values
    plt.xticks(x, labels, rotation=30, ha="right")
    plt.ylabel("Post-T events (millions)")
    plt.title("Post-T Events by User State Category")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_sequence_lengths(path: Path, rows: list[dict[str, Any]]) -> None:
    labels = [row["candidate_id"] for row in rows]
    p50 = [row["train_positive_seq_len_p50"] for row in rows]
    p90 = [row["train_positive_seq_len_p90"] for row in rows]
    p99 = [row["train_positive_seq_len_p99"] for row in rows]
    x = np.arange(len(labels))
    width = 0.25
    plt.figure(figsize=(11, 5))
    plt.bar(x - width, p50, width, label="p50", color="#2A9D8F")
    plt.bar(x, p90, width, label="p90", color="#F4A261")
    plt.bar(x + width, p99, width, label="p99", color="#E76F51")
    plt.xticks(x, labels, rotation=30, ha="right")
    plt.ylabel("Positive sequence length")
    plt.title("Pre-T Train Eligible Positive Sequence Length")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_load_proxy(path: Path, rows: list[dict[str, Any]]) -> None:
    labels = [row["candidate_id"] for row in rows]
    values = [row["online_recompute_rows_est"] / 1_000_000_000 for row in rows]
    plt.figure(figsize=(11, 5))
    plt.bar(labels, values, color="#E76F51")
    plt.xticks(rotation=30, ha="right")
    plt.ylabel("Estimated rows recomputed (billions)")
    plt.title("Online Extract/Assign Load Amplification Proxy")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def write_report(path: Path, args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    generated_at = datetime.now(timezone.utc).strftime(RATED_AT_FORMAT)
    lines: list[str] = []
    lines.append("# Temporal Cutoff 2020 Pipeline Deep Dive")
    lines.append("")
    lines.append(f"Generated at: {generated_at}")
    lines.append("")
    lines.append("## 파이프라인 기준")
    lines.append("")
    lines.append(
        md_table(
            ["Stage", "현재 기준", "이 EDA에서 보는 값"],
            [
                [
                    "batch train",
                    f"z-score positive, min interactions {args.train_min_interactions}, activity {args.min_activity_days}d",
                    "train eligible user, pre-T positive sequence length, training window sample",
                ],
                [
                    "canonical extract",
                    f"min interactions {args.canonical_min_interactions}",
                    "초기 interest state를 만들 수 있는 canonical seed user",
                ],
                [
                    "item2idx",
                    "train eligible positive movie만 포함",
                    "post-T known/unknown item event share",
                ],
                [
                    "online extract",
                    "event마다 해당 user active positive 전체 embedding 재계산",
                    "online recompute rows proxy",
                ],
                [
                    "interest/refit",
                    f"refit_min {args.refit_min_events}, assign_trigger {args.assign_trigger_count}, outlier {args.outlier_trigger_count}",
                    "seeded/no-seed refit candidate user와 refit cluster row",
                ],
            ],
        )
    )
    lines.append("")
    lines.append("## 후보별 요약")
    lines.append("")
    lines.append(
        md_table(
            [
                "T",
                "Pre raw",
                "Post raw",
                "Train users",
                "Canonical users",
                "Item vocab",
                "Train seq p50/p90/p99",
                "Known post item",
                "Post events to canonical",
                "Online rows/event",
                "Refit users",
            ],
            [
                [
                    f"{row['candidate_id']}<br>{row['cutoff']}",
                    format_int(row["pre_raw_events"]),
                    format_int(row["post_raw_events"]),
                    format_int(row["train_eligible_users"]),
                    format_int(row["canonical_eligible_users"]),
                    format_int(row["item_vocab_size"]),
                    (
                        f"{format_int(row['train_positive_seq_len_p50'])}/"
                        f"{format_int(row['train_positive_seq_len_p90'])}/"
                        f"{format_int(row['train_positive_seq_len_p99'])}"
                    ),
                    f"{format_pct(row['post_known_item_event_share'])}",
                    f"{format_pct(row['post_events_canonical_seed_user_share'])}",
                    format_int(row["online_recompute_rows_per_event_est"]),
                    (
                        f"seeded {format_int(row['refit_candidate_seeded_assign_trigger_users'])}, "
                        f"no-seed {format_int(row['refit_candidate_no_seed_min_events_users'])}"
                    ),
                ]
                for row in rows
            ],
        )
    )
    lines.append("")
    lines.append("![Post Events by User Category](temporal_cutoff_2020_post_event_categories.png)")
    lines.append("")
    lines.append("![Sequence Lengths](temporal_cutoff_2020_sequence_lengths.png)")
    lines.append("")
    lines.append("![Load Proxy](temporal_cutoff_2020_online_load_proxy.png)")
    lines.append("")
    lines.append("## User/Sequence 해석")
    lines.append("")
    lines.append(
        md_table(
            [
                "T",
                "Train windows",
                "Post user events p50/p90/p99/max",
                "Final active known seq p50/p90/p99/max",
                "Post known positive proxy",
            ],
            [
                [
                    row["candidate_id"],
                    format_int(row["pre_training_window_samples"]),
                    (
                        f"{format_int(row['post_user_events_p50'])}/"
                        f"{format_int(row['post_user_events_p90'])}/"
                        f"{format_int(row['post_user_events_p99'])}/"
                        f"{format_int(row['post_user_events_max'])}"
                    ),
                    (
                        f"{format_int(row['final_active_known_seq_len_p50'])}/"
                        f"{format_int(row['final_active_known_seq_len_p90'])}/"
                        f"{format_int(row['final_active_known_seq_len_p99'])}/"
                        f"{format_int(row['final_active_known_seq_len_max'])}"
                    ),
                    (
                        f"{format_int(row['post_positive_proxy_known_events'])} "
                        f"({format_pct(row['post_positive_proxy_known_event_share'])})"
                    ),
                ]
                for row in rows
            ],
        )
    )
    lines.append("")
    lines.append("## Post-T 이벤트가 들어가는 위치")
    lines.append("")
    lines.append(
        md_table(
            [
                "T",
                "Canonical seed",
                "Train only",
                "Existing untrained",
                "New user",
                "Unknown item events",
                "Hourly p99/max",
            ],
            [
                [
                    row["candidate_id"],
                    f"{format_int(row['post_events_canonical_seed_user'])} ({format_pct(row['post_events_canonical_seed_user_share'])})",
                    f"{format_int(row['post_events_train_only_user'])} ({format_pct(row['post_events_train_only_user_share'])})",
                    f"{format_int(row['post_events_existing_untrained_user'])} ({format_pct(row['post_events_existing_untrained_user_share'])})",
                    f"{format_int(row['post_events_new_user'])} ({format_pct(row['post_events_new_user_share'])})",
                    f"{format_int(row['post_unknown_item_events'])} ({format_pct(row['post_unknown_item_event_share'])})",
                    f"{format_int(row['post_hourly_events_p99'])}/{format_int(row['post_hourly_events_max'])}",
                ]
                for row in rows
            ],
        )
    )
    lines.append("")
    lines.append("## 결론")
    lines.append("")
    lines.append("- 2020 전후 cutoff는 `T`가 늦어질수록 train/canonical user와 item vocab은 늘지만, post-T 부하 관찰량은 줄어든다.")
    lines.append("- 현재 replay stage는 event 하나당 새 event 하나만 처리하는 구조가 아니라, 해당 user의 active known positive sequence를 반복 재계산/재스캔한다.")
    lines.append("- 따라서 post raw event 수보다 `online rows/event`와 `final active known sequence length`가 실제 CPU/GPU/IO 부하를 더 잘 설명한다.")
    lines.append("- 초기 interest state를 canonical user에만 seed한다면, post-T 이벤트의 상당 부분은 no-interest 상태로 들어오므로 refit pressure가 커진다.")
    lines.append("- 다음 구현에서는 train cutoff, canonical cutoff, state seed cutoff를 같은 `T`로 묶고, canonical min interactions를 train과 맞출지 별도 결정해야 한다.")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_dir = ensure_dir(args.output_dir)
    cutoffs = parse_cutoffs(args.cutoffs)

    metrics, item_vocab, train_users, canonical_users, pre_counts, train_sequence_lengths = first_pass(
        args.ratings_sequence,
        cutoffs,
        train_min_interactions=args.train_min_interactions,
        canonical_min_interactions=args.canonical_min_interactions,
        min_activity_days=args.min_activity_days,
        seq_len=args.seq_len,
        stride=args.stride,
    )
    post_user_event_counts, post_known_positive_counts, final_active_known_lengths, daily_counts, hourly_counts = second_pass(
        args.ratings_sequence,
        cutoffs,
        metrics,
        item_vocab,
        train_users,
        canonical_users,
        pre_counts,
        positive_rating_threshold=args.positive_rating_threshold,
        refit_min_events=args.refit_min_events,
        assign_trigger_count=args.assign_trigger_count,
    )

    rows = [metrics[cutoff.candidate_id] for cutoff in cutoffs]
    add_distribution_columns(
        rows,
        train_sequence_lengths=train_sequence_lengths,
        post_user_event_counts=post_user_event_counts,
        post_known_positive_counts=post_known_positive_counts,
        final_active_known_lengths=final_active_known_lengths,
        daily_counts=daily_counts,
        hourly_counts=hourly_counts,
    )

    pl.DataFrame(rows).write_csv(output_dir / "temporal_cutoff_2020_deep_dive.csv")
    plot_stacked_post_categories(output_dir / "temporal_cutoff_2020_post_event_categories.png", rows)
    plot_sequence_lengths(output_dir / "temporal_cutoff_2020_sequence_lengths.png", rows)
    plot_load_proxy(output_dir / "temporal_cutoff_2020_online_load_proxy.png", rows)
    write_report(output_dir / "temporal_cutoff_2020_deep_dive_report.md", args, rows)

    print(f"Wrote report: {output_dir / 'temporal_cutoff_2020_deep_dive_report.md'}")
    print(f"Wrote CSV: {output_dir / 'temporal_cutoff_2020_deep_dive.csv'}")


if __name__ == "__main__":
    main()
