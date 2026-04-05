from __future__ import annotations

import bisect
import json
import math
import os
import re
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BASE_DIR)

os.environ.setdefault(
    "MPLCONFIGDIR",
    os.path.join(BASE_DIR, "eda_outputs", ".mplconfig"),
)

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

EDA_OUTPUT_DIR = os.path.join(BASE_DIR, "eda_outputs")
YEAR_PATTERN = re.compile(r"\((\d{4})\)\s*$")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


ensure_dir(EDA_OUTPUT_DIR)
ensure_dir(os.environ["MPLCONFIGDIR"])


def dataset_output_dir(name: str) -> str:
    return ensure_dir(os.path.join(EDA_OUTPUT_DIR, name))


def resolve_existing_dir(description: str, candidates: Iterable[str]) -> str:
    checked: list[str] = []
    for path in candidates:
        normalized = os.path.abspath(path)
        checked.append(normalized)
        if os.path.isdir(normalized):
            return normalized
    raise FileNotFoundError(f"Could not find {description}. Checked: {', '.join(checked)}")


def resolve_existing_file(description: str, candidates: Iterable[str]) -> str:
    checked: list[str] = []
    for path in candidates:
        normalized = os.path.abspath(path)
        checked.append(normalized)
        if os.path.isfile(normalized):
            return normalized
    raise FileNotFoundError(f"Could not find {description}. Checked: {', '.join(checked)}")


def format_int(value: int) -> str:
    return f"{value:,}"


def format_float(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def format_count_stat(value: float) -> str:
    return format_int(int(round(value)))


def percent(numerator: int | float, denominator: int | float, digits: int = 2) -> str:
    if denominator == 0:
        return f"{0:.{digits}f}%"
    return format_float((numerator / denominator) * 100, digits) + "%"


def quantile_summary(values: np.ndarray) -> dict[str, float]:
    if len(values) == 0:
        return {key: 0.0 for key in ["min", "p25", "median", "p75", "p90", "p99", "max", "mean"]}
    return {
        "min": float(np.min(values)),
        "p25": float(np.percentile(values, 25)),
        "median": float(np.percentile(values, 50)),
        "p75": float(np.percentile(values, 75)),
        "p90": float(np.percentile(values, 90)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
    }


def threshold_stats_from_array(values: np.ndarray, thresholds: list[int]) -> list[tuple[int, int, float]]:
    total = len(values)
    stats = []
    for threshold in thresholds:
        matched = int(np.sum(values >= threshold))
        share = 0.0 if total == 0 else (matched / total) * 100
        stats.append((threshold, matched, share))
    return stats


def threshold_rows_from_array(values: np.ndarray, thresholds: list[int], label_suffix: str) -> list[list[str]]:
    rows = []
    for threshold, matched, share in threshold_stats_from_array(values, thresholds):
        rows.append([f">= {format_int(threshold)} {label_suffix}", format_int(matched), format_float(share, 2) + "%"])
    return rows


def at_most_stats(values: np.ndarray, thresholds: list[int]) -> list[tuple[int, int, float]]:
    sorted_values = np.sort(values)
    total = len(sorted_values)
    stats = []
    for threshold in thresholds:
        matched = int(np.searchsorted(sorted_values, threshold, side="right"))
        share = 0.0 if total == 0 else (matched / total) * 100
        stats.append((threshold, matched, share))
    return stats


def concentration_stats(values: np.ndarray, fractions: Iterable[float] = (0.01, 0.05, 0.10)) -> list[tuple[float, int, int, float]]:
    if len(values) == 0:
        return []
    sorted_values = np.sort(values)
    total_entities = len(sorted_values)
    total_weight = float(np.sum(sorted_values))
    rows = []
    for fraction in fractions:
        top_n = max(1, math.ceil(total_entities * fraction))
        top_weight = float(np.sum(sorted_values[-top_n:]))
        share = 0.0 if total_weight == 0 else (top_weight / total_weight) * 100
        rows.append((fraction, top_n, total_entities, share))
    return rows


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    divider_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    body_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, divider_line, *body_lines])


def write_markdown(path: str, lines: list[str]) -> str:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


def parse_movie_year(title: str) -> int | None:
    match = YEAR_PATTERN.search(title)
    if not match:
        return None
    return int(match.group(1))


def make_year_lookup(start_year: int = 1970, end_year: int = 2030) -> tuple[list[int], list[int]]:
    years = list(range(start_year, end_year + 1))
    boundaries = [int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()) for year in years]
    return boundaries, years


YEAR_BOUNDARIES, YEAR_VALUES = make_year_lookup()


def timestamp_to_year(timestamp: int) -> int:
    idx = bisect.bisect_right(YEAR_BOUNDARIES, timestamp) - 1
    idx = max(0, min(idx, len(YEAR_VALUES) - 1))
    return YEAR_VALUES[idx]


def utc_date_str(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")


def build_user_sequence_arrays(
    ratings_per_user: dict[int, int],
    min_timestamp_per_user: dict[int, int],
    max_timestamp_per_user: dict[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    user_ids = list(ratings_per_user.keys())
    user_sequence_lengths = np.fromiter((ratings_per_user[user_id] for user_id in user_ids), dtype=np.int64)
    user_sequence_spans = np.fromiter(
        ((max_timestamp_per_user[user_id] - min_timestamp_per_user[user_id]) / 86400 for user_id in user_ids),
        dtype=np.float64,
    )
    return user_sequence_lengths, user_sequence_spans


def span_bucket_stats(spans: np.ndarray) -> list[tuple[float | None, float | None, int, float]]:
    bounds = [
        (None, 0.1),
        (0.1, 1.0),
        (1.0, 7.0),
        (7.0, 30.0),
        (30.0, 365.0),
        (365.0, None),
    ]
    total = len(spans)
    rows = []
    for lower, upper in bounds:
        if lower is None:
            mask = spans <= upper
        elif upper is None:
            mask = spans > lower
        else:
            mask = (spans > lower) & (spans <= upper)
        count = int(np.sum(mask))
        share = 0.0 if total == 0 else (count / total) * 100
        rows.append((lower, upper, count, share))
    return rows


def span_stats_by_sequence_threshold(
    lengths: np.ndarray,
    spans: np.ndarray,
    thresholds: list[int],
) -> list[tuple[int, int, float, float]]:
    rows = []
    for threshold in thresholds:
        mask = lengths >= threshold
        filtered_spans = spans[mask]
        if len(filtered_spans) == 0:
            rows.append((threshold, 0, 0.0, 0.0))
            continue
        rows.append(
            (
                threshold,
                int(np.sum(mask)),
                float(np.percentile(filtered_spans, 50)),
                float(np.mean(filtered_spans)),
            )
        )
    return rows


def load_jsonl(path: str) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def plot_rating_distribution(output_dir: str, rating_value_counts: dict[float, int], filename: str = "ratings_distribution.png") -> str:
    path = os.path.join(output_dir, filename)
    ratings = sorted(rating_value_counts.keys())
    counts = [rating_value_counts[rating] for rating in ratings]

    plt.figure(figsize=(10, 5))
    plt.bar([str(rating) for rating in ratings], counts, color="#2A9D8F")
    plt.title("Rating Distribution")
    plt.xlabel("Rating")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def plot_bar_chart(
    output_dir: str,
    filename: str,
    labels: list[str],
    values: list[int | float],
    title: str,
    x_label: str,
    y_label: str,
    color: str = "#2A9D8F",
) -> str:
    path = os.path.join(output_dir, filename)
    x = np.arange(len(labels))

    plt.figure(figsize=(10, 5))
    bars = plt.bar(x, values, color=color, width=0.65)
    plt.xticks(x, labels)
    plt.xlabel(x_label)
    plt.ylabel(y_label)
    plt.title(title)

    max_value = max(values) if values else 0
    for bar, value in zip(bars, values):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            format_int(int(value)) if float(value).is_integer() else format_float(float(value), 2),
            ha="center",
            va="bottom",
            fontsize=8,
        )

    if max_value > 0:
        plt.ylim(0, max_value * 1.15)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def plot_dual_line_chart(
    output_dir: str,
    filename: str,
    first_series: dict[int, int],
    second_series: dict[int, int],
    first_label: str,
    second_label: str,
    title: str,
) -> str:
    path = os.path.join(output_dir, filename)
    years = sorted(set(first_series) | set(second_series))
    first_counts = [first_series.get(year, 0) for year in years]
    second_counts = [second_series.get(year, 0) for year in years]

    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.plot(years, first_counts, color="#264653", linewidth=2, label=first_label)
    ax1.set_xlabel("Year")
    ax1.set_ylabel(first_label, color="#264653")
    ax1.tick_params(axis="y", labelcolor="#264653")

    ax2 = ax1.twinx()
    ax2.plot(years, second_counts, color="#E76F51", linewidth=2, label=second_label)
    ax2.set_ylabel(second_label, color="#E76F51")
    ax2.tick_params(axis="y", labelcolor="#E76F51")

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_long_tail_histogram(output_dir: str, values: np.ndarray, title: str, x_label: str, filename: str) -> str:
    path = os.path.join(output_dir, filename)
    positive_values = values[values > 0]
    log_values = np.log10(positive_values)

    plt.figure(figsize=(10, 5))
    plt.hist(log_values, bins=50, color="#F4A261", edgecolor="white")
    plt.title(title)
    plt.xlabel(f"log10({x_label})")
    plt.ylabel("Frequency")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def plot_threshold_coverage(
    output_dir: str,
    filename: str,
    values: np.ndarray,
    thresholds: list[int],
    title: str,
    x_label: str,
    entity_label: str,
) -> str:
    path = os.path.join(output_dir, filename)
    stats = threshold_stats_from_array(values, thresholds)
    x = np.arange(len(stats))
    counts = [matched for _, matched, _ in stats]
    shares = [share for _, _, share in stats]
    labels = [f">= {threshold}" for threshold, _, _ in stats]

    fig, ax1 = plt.subplots(figsize=(10, 5))
    bars = ax1.bar(x, counts, color="#2A9D8F", width=0.6)
    ax1.set_xticks(x, labels)
    ax1.set_xlabel(x_label)
    ax1.set_ylabel(entity_label, color="#264653")
    ax1.tick_params(axis="y", labelcolor="#264653")

    ax2 = ax1.twinx()
    ax2.plot(x, shares, color="#E76F51", marker="o", linewidth=2)
    ax2.set_ylabel("Share (%)", color="#E76F51")
    ax2.tick_params(axis="y", labelcolor="#E76F51")
    ax2.set_ylim(0, 105)

    for bar, share in zip(bars, shares):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{share:.1f}%",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_span_buckets(output_dir: str, filename: str, spans: np.ndarray, title: str) -> str:
    path = os.path.join(output_dir, filename)
    stats = span_bucket_stats(spans)
    labels = ["<=0.1d", "0.1-1d", "1-7d", "7-30d", "30-365d", ">365d"]
    counts = [count for _, _, count, _ in stats]
    shares = [share for _, _, _, share in stats]
    x = np.arange(len(labels))

    plt.figure(figsize=(10, 5))
    bars = plt.bar(x, shares, color="#F4A261", width=0.65)
    plt.xticks(x, labels)
    plt.xlabel("User activity span bucket")
    plt.ylabel("Share of users (%)")
    plt.title(title)
    plt.ylim(0, max(shares) * 1.15 if shares else 1)

    for bar, share, count in zip(bars, shares, counts):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{share:.1f}%\n({format_int(count)})",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return path
