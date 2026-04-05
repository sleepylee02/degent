#!/usr/bin/env python3
"""Comprehensive EDA for processed movie and rating datasets.

Sections:
  1. Movie Drop Comparison — before vs after drop
  2. Dropped Movie Deep Dive — characteristics of removed movies
  3. (Rating drop analysis skipped)
  4. Final Rating Data Analysis — sparsity, temporal, long-tail, cold-start, sequences
"""

from __future__ import annotations

import json
import os
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DEFAULT_MOVIES_BEFORE_PATH = REPO_ROOT / "data" / "movies_processed.csv"
DEFAULT_MOVIES_AFTER_PATH = REPO_ROOT / "data" / "movies_processed_drop.csv"
DEFAULT_RATINGS_PATH = REPO_ROOT / "data" / "ratings_drop.csv"
DEFAULT_RATINGS_SEQUENCE_PATH = REPO_ROOT / "data" / "ratings_drop_processed.jsonl"
DEFAULT_MOVIE_DROP_REPORT_PATH = REPO_ROOT / "preprocess" / "drop_movie" / "validation_report.json"
DEFAULT_SEQUENCE_REPORT_PATH = REPO_ROOT / "preprocess" / "process_rating" / "validation_report.json"

RATED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_movies_csv(path: Path) -> pl.DataFrame:
    return pl.read_csv(
        path,
        schema_overrides={
            "movieId": pl.Int64,
            "title": pl.String,
            "releaseYear": pl.Int64,
            "genres": pl.String,
            "tag": pl.String,
            "tagCount": pl.Int64,
            "ratingAvg": pl.Float64,
            "ratingCount": pl.Int64,
            "imdbId": pl.Int64,
            "tmdbId": pl.Int64,
        },
        null_values="",
    )


def scan_ratings_csv(path: Path) -> pl.LazyFrame:
    return pl.scan_csv(
        path,
        schema_overrides={
            "userId": pl.Int64,
            "movieId": pl.Int64,
            "rating": pl.Float64,
            "ratedAt": pl.String,
        },
    )


def parse_json_list(value: str | None) -> list[str]:
    if value is None or value == "":
        return []
    return json.loads(value)


def parse_iso_utc(value: str) -> datetime:
    return datetime.strptime(value, RATED_AT_FORMAT).replace(tzinfo=timezone.utc)


def iso_to_year(value: str) -> int:
    return int(value[:4])


def format_int(value: int) -> str:
    return f"{value:,}"


def format_float(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def percent(numerator: int | float, denominator: int | float) -> str:
    if denominator == 0:
        return "0.00%"
    return f"{(numerator / denominator) * 100:.2f}%"


def quantile_summary(values: np.ndarray) -> dict[str, float]:
    if len(values) == 0:
        return {k: 0.0 for k in ["min", "p25", "median", "p75", "p90", "p99", "max", "mean"]}
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


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, divider, *body])


def md_quantile_table(label: str, stats: dict[str, float], fmt: str = "int") -> str:
    fn = format_int if fmt == "int" else (lambda v: format_float(v))
    rows = [[k, fn(int(round(stats[k]))) if fmt == "int" else fn(stats[k])] for k in stats]
    return md_table(["Statistic", label], rows)


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def concentration_stats(
    sorted_desc: np.ndarray, fractions: list[float] = [0.01, 0.05, 0.10]
) -> list[dict[str, object]]:
    """Top-fraction concentration: what share of total does the top X% account for?"""
    total = float(sorted_desc.sum())
    n = len(sorted_desc)
    results = []
    for frac in fractions:
        top_n = max(1, int(np.ceil(n * frac)))
        top_sum = float(sorted_desc[:top_n].sum())
        results.append({
            "fraction": frac,
            "top_n": top_n,
            "share": top_sum / total if total > 0 else 0.0,
        })
    return results


def gini_coefficient(values: np.ndarray) -> float:
    """Gini coefficient for measuring inequality."""
    if len(values) == 0:
        return 0.0
    sorted_vals = np.sort(values).astype(float)
    n = len(sorted_vals)
    index = np.arange(1, n + 1)
    return float((2 * np.sum(index * sorted_vals) - (n + 1) * np.sum(sorted_vals)) / (n * np.sum(sorted_vals)))


def threshold_coverage(counts: np.ndarray, thresholds: list[int]) -> list[dict[str, object]]:
    """For each threshold, how many items have >= threshold interactions, and what share of total interactions."""
    total_interactions = int(counts.sum())
    total_items = len(counts)
    results = []
    for t in thresholds:
        mask = counts >= t
        items_above = int(mask.sum())
        interactions_above = int(counts[mask].sum())
        results.append({
            "threshold": t,
            "items": items_above,
            "items_pct": items_above / total_items if total_items > 0 else 0.0,
            "interactions": interactions_above,
            "interactions_pct": interactions_above / total_interactions if total_interactions > 0 else 0.0,
        })
    return results


SPAN_BUCKETS = [
    ("<=0.1d", 0.0, 0.1),
    ("0.1-1d", 0.1, 1.0),
    ("1-7d", 1.0, 7.0),
    ("7-30d", 7.0, 30.0),
    ("30-365d", 30.0, 365.0),
    (">365d", 365.0, float("inf")),
]


def span_bucket_stats(span_days: np.ndarray) -> list[dict[str, object]]:
    total = len(span_days)
    results = []
    for label, lo, hi in SPAN_BUCKETS:
        if hi == float("inf"):
            count = int((span_days > lo).sum())
        elif lo == 0.0:
            count = int((span_days <= hi).sum())
        else:
            count = int(((span_days > lo) & (span_days <= hi)).sum())
        results.append({"label": label, "count": count, "pct": count / total if total > 0 else 0.0})
    return results


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------


def plot_bar(path: Path, labels: list[str], values: list[float], title: str, xlabel: str, ylabel: str) -> None:
    x = np.arange(len(labels))
    plt.figure(figsize=(10, 5))
    bars = plt.bar(x, values, color="#2A9D8F", width=0.65)
    plt.xticks(x, labels, rotation=45, ha="right")
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.title(title)
    for bar, val in zip(bars, values):
        text = format_int(int(val)) if float(val).is_integer() else format_float(val)
        plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), text, ha="center", va="bottom", fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_histogram(path: Path, values: np.ndarray, title: str, xlabel: str, log_scale: bool = False) -> None:
    plt.figure(figsize=(10, 5))
    plot_values = np.log10(values[values > 0]) if log_scale else values
    counts, bins, patches = plt.hist(plot_values, bins=50, color="#F4A261", edgecolor="white")
    for i, (count, patch) in enumerate(zip(counts, patches)):
        if count > 0 and i % 4 == 0:
            plt.text(
                patch.get_x() + patch.get_width() / 2, count,
                format_int(int(count)), ha="center", va="bottom", fontsize=7,
            )
    plt.title(title)
    plt.xlabel(f"log10({xlabel})" if log_scale else xlabel)
    plt.ylabel("Frequency")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_comparison_bar(path: Path, labels: list[str], before: list[float], after: list[float], title: str) -> None:
    x = np.arange(len(labels))
    width = 0.35
    plt.figure(figsize=(10, 5))
    bars_b = plt.bar(x - width / 2, before, width, label="Before drop", color="#264653")
    bars_a = plt.bar(x + width / 2, after, width, label="After drop", color="#2A9D8F")
    for bar, val in zip(bars_b, before):
        plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), format_int(int(val)), ha="center", va="bottom", fontsize=7, color="#264653")
    for bar, val in zip(bars_a, after):
        plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), format_int(int(val)), ha="center", va="bottom", fontsize=7, color="#2A9D8F")
    plt.xticks(x, labels, rotation=45, ha="right")
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_comparison_histogram(
    path: Path, vals_a: np.ndarray, vals_b: np.ndarray, label_a: str, label_b: str, title: str, xlabel: str,
) -> None:
    plt.figure(figsize=(10, 5))
    bins = np.linspace(min(vals_a.min(), vals_b.min()), max(vals_a.max(), vals_b.max()), 50)
    counts_a, _, patches_a = plt.hist(vals_a, bins=bins, alpha=0.6, label=label_a, color="#E76F51", edgecolor="white")
    counts_b, _, patches_b = plt.hist(vals_b, bins=bins, alpha=0.6, label=label_b, color="#2A9D8F", edgecolor="white")
    for counts, patches, color in [(counts_a, patches_a, "#E76F51"), (counts_b, patches_b, "#2A9D8F")]:
        for i, (count, patch) in enumerate(zip(counts, patches)):
            if count > 0 and i % 4 == 0:
                plt.text(
                    patch.get_x() + patch.get_width() / 2, count,
                    format_int(int(count)), ha="center", va="bottom", fontsize=7, color=color,
                )
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Frequency")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_threshold_coverage(
    path: Path, thresholds: list[int], items_counts: list[int], interaction_pcts: list[float], title: str,
    xlabel: str, ylabel_left: str, ylabel_right: str,
) -> None:
    x = np.arange(len(thresholds))
    fig, ax1 = plt.subplots(figsize=(10, 5))
    bars = ax1.bar(x, items_counts, color="#264653", width=0.5, label=ylabel_left)
    ax1.set_xlabel(xlabel)
    ax1.set_ylabel(ylabel_left, color="#264653")
    ax1.set_xticks(x)
    ax1.set_xticklabels([str(t) for t in thresholds])
    for bar, val in zip(bars, items_counts):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), format_int(val), ha="center", va="bottom", fontsize=8)

    ax2 = ax1.twinx()
    ax2.plot(x, [p * 100 for p in interaction_pcts], color="#E9C46A", marker="o", linewidth=2, label=ylabel_right)
    ax2.set_ylabel(ylabel_right, color="#E9C46A")
    ax2.set_ylim(0, 105)
    for i, p in enumerate(interaction_pcts):
        ax2.text(i, p * 100 + 2, f"{p * 100:.1f}%", ha="center", fontsize=8, color="#E9C46A")

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_span_buckets(path: Path, buckets: list[dict[str, object]], title: str) -> None:
    labels = [b["label"] for b in buckets]
    counts = [b["count"] for b in buckets]
    pcts = [b["pct"] for b in buckets]

    x = np.arange(len(labels))
    fig, ax1 = plt.subplots(figsize=(10, 5))
    bars = ax1.bar(x, counts, color="#2A9D8F", width=0.5)
    ax1.set_xlabel("Activity Span")
    ax1.set_ylabel("Users", color="#2A9D8F")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)

    ax2 = ax1.twinx()
    ax2.plot(x, [p * 100 for p in pcts], color="#E76F51", marker="o", linewidth=2)
    ax2.set_ylabel("Share (%)", color="#E76F51")
    ax2.set_ylim(0, max(p * 100 for p in pcts) * 1.3 if pcts else 100)
    for i, (c, p) in enumerate(zip(counts, pcts)):
        ax1.text(bars[i].get_x() + bars[i].get_width() / 2, bars[i].get_height(), format_int(c), ha="center", va="bottom", fontsize=8)
        ax2.text(i, p * 100 + 1, f"{p * 100:.1f}%", ha="center", fontsize=8, color="#E76F51")

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_dual_line(
    path: Path, x_labels: list[str], y1: list[float], y2: list[float],
    title: str, xlabel: str, ylabel1: str, ylabel2: str,
) -> None:
    x = np.arange(len(x_labels))
    fig, ax1 = plt.subplots(figsize=(12, 5))
    bars = ax1.bar(x, y1, color="#264653", width=0.6, label=ylabel1)
    ax1.set_xlabel(xlabel)
    ax1.set_ylabel(ylabel1, color="#264653")
    ax1.set_xticks(x[::max(1, len(x) // 15)])
    ax1.set_xticklabels([x_labels[i] for i in range(0, len(x_labels), max(1, len(x_labels) // 15))], rotation=45, ha="right")
    for bar, val in zip(bars, y1):
        if val > 0:
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), format_int(int(val)), ha="center", va="bottom", fontsize=6, color="#264653")

    ax2 = ax1.twinx()
    ax2.plot(x, y2, color="#E9C46A", linewidth=2, label=ylabel2)
    ax2.set_ylabel(ylabel2, color="#E9C46A")
    ax2.set_ylim(0, 105)

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Genre helpers
# ---------------------------------------------------------------------------


def explode_genres(df: pl.DataFrame) -> pl.Series:
    return df["genres"].map_elements(parse_json_list, return_dtype=pl.List(pl.String)).explode().drop_nulls()


def genre_value_counts(genres_series: pl.Series) -> pl.DataFrame:
    return genres_series.value_counts().sort("count", descending=True)


# ---------------------------------------------------------------------------
# Section 1: Movie Drop Comparison
# ---------------------------------------------------------------------------


def section_movie_drop_comparison(
    before: pl.DataFrame,
    after: pl.DataFrame,
    drop_report: dict[str, object],
    out: Path,
) -> list[str]:
    lines: list[str] = []
    lines.append("## 1. Movie Drop 전후 비교")
    lines.append("")

    summary = drop_report["summary"]
    counts = drop_report["counts"]

    lines.append("### 전체 요약")
    lines.append("")
    lines.append(
        md_table(
            ["Metric", "Value"],
            [
                ["Before (movies_processed)", format_int(summary["movies_input"])],
                ["After (movies_processed_drop)", format_int(summary["movies_output"])],
                ["Dropped", format_int(summary["movies_dropped"])],
                ["Drop ratio", f"{summary['drop_ratio_percent']:.2f}%"],
            ],
        )
    )
    lines.append("")

    # Drop reasons
    lines.append("### Drop 사유별 집계")
    lines.append("")
    lines.append(
        md_table(
            ["Issue", "Count", "Share of dropped"],
            [
                ["missing_genres", format_int(counts["missing_genres"]), percent(counts["missing_genres"], summary["movies_dropped"])],
                ["missing_ratings", format_int(counts["missing_ratings"]), percent(counts["missing_ratings"], summary["movies_dropped"])],
                ["title_without_year", format_int(counts["title_without_year"]), percent(counts["title_without_year"], summary["movies_dropped"])],
            ],
        )
    )
    lines.append("")

    # Issue combinations
    issue_combos = drop_report.get("issue_combinations", [])
    if issue_combos:
        lines.append("### Issue 조합 (겹침 분석)")
        lines.append("")
        combo_rows = []
        for combo in issue_combos:
            issues_str = " + ".join(combo["issues"])
            combo_rows.append([issues_str, format_int(combo["count"]), percent(combo["count"], summary["movies_dropped"])])
        lines.append(md_table(["Issue Combination", "Count", "Share"], combo_rows))
        lines.append("")

    # Key metric changes
    before_rating_avg = before["ratingAvg"].drop_nulls().mean()
    after_rating_avg = after["ratingAvg"].drop_nulls().mean()

    lines.append("### 주요 지표 변화")
    lines.append("")
    lines.append(
        md_table(
            ["Metric", "Before", "After"],
            [
                ["Total movies", format_int(before.height), format_int(after.height)],
                ["Mean ratingAvg", format_float(before_rating_avg), format_float(after_rating_avg)],
                ["Median ratingCount", format_int(int(before["ratingCount"].median())), format_int(int(after["ratingCount"].median()))],
                ["Median tagCount", format_int(int(before["tagCount"].median())), format_int(int(after["tagCount"].median()))],
            ],
        )
    )
    lines.append("")

    # Genre comparison
    before_genres = explode_genres(before)
    after_genres = explode_genres(after)
    before_gc = genre_value_counts(before_genres).head(10)
    after_gc = genre_value_counts(after_genres)

    top_genres = before_gc["genres"].to_list()
    after_map = dict(zip(after_gc["genres"].to_list(), after_gc["count"].to_list()))
    genre_before = [int(before_gc.filter(pl.col("genres") == g)["count"].item()) for g in top_genres]
    genre_after = [after_map.get(g, 0) for g in top_genres]

    plot_comparison_bar(out / "genre_comparison.png", top_genres, genre_before, genre_after, "Genre Distribution: Before vs After Drop")

    lines.append("### 장르 분포 비교 (Top 10)")
    lines.append("")
    lines.append(
        md_table(
            ["Genre", "Before", "After", "Dropped"],
            [[g, format_int(b), format_int(a), format_int(b - a)] for g, b, a in zip(top_genres, genre_before, genre_after)],
        )
    )
    lines.append("")
    lines.append("![Genre Comparison](genre_comparison.png)")
    lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Section 2: Dropped Movie Deep Dive
# ---------------------------------------------------------------------------


def section_dropped_movie_deep_dive(
    before: pl.DataFrame,
    after: pl.DataFrame,
    drop_report: dict[str, object],
    out: Path,
) -> list[str]:
    lines: list[str] = []
    lines.append("## 2. Dropped Movie 심층 분석")
    lines.append("")

    kept_ids = set(after["movieId"].to_list())
    dropped = before.filter(~pl.col("movieId").is_in(kept_ids))
    kept = after

    lines.append(f"Dropped movie 수: {format_int(dropped.height)}")
    lines.append("")

    # --- releaseYear comparison ---
    lines.append("### releaseYear 분포: Dropped vs Kept")
    lines.append("")
    dropped_years = dropped["releaseYear"].drop_nulls().to_numpy()
    kept_years = kept["releaseYear"].drop_nulls().to_numpy()

    dropped_year_stats = quantile_summary(dropped_years)
    kept_year_stats = quantile_summary(kept_years)
    lines.append(
        md_table(
            ["Statistic", "Dropped", "Kept"],
            [[k, format_int(int(round(dropped_year_stats[k]))), format_int(int(round(kept_year_stats[k])))] for k in dropped_year_stats],
        )
    )
    lines.append("")
    plot_comparison_histogram(
        out / "dropped_releaseYear_comparison.png",
        dropped_years, kept_years, "Dropped", "Kept",
        "Release Year: Dropped vs Kept", "releaseYear",
    )
    lines.append("![Release Year Comparison](dropped_releaseYear_comparison.png)")
    lines.append("")

    # --- ratingCount comparison ---
    lines.append("### ratingCount 분포: Dropped vs Kept")
    lines.append("")
    dropped_rc = dropped["ratingCount"].to_numpy()
    kept_rc = kept["ratingCount"].to_numpy()
    dropped_rc_stats = quantile_summary(dropped_rc)
    kept_rc_stats = quantile_summary(kept_rc)
    lines.append(
        md_table(
            ["Statistic", "Dropped", "Kept"],
            [[k, format_int(int(round(dropped_rc_stats[k]))), format_int(int(round(kept_rc_stats[k])))] for k in dropped_rc_stats],
        )
    )
    lines.append("")
    zero_rc = int((dropped_rc == 0).sum())
    lines.append(f"Dropped movie 중 ratingCount == 0: {format_int(zero_rc)} ({percent(zero_rc, dropped.height)})")
    lines.append("")

    # --- ratingAvg comparison ---
    lines.append("### ratingAvg 분포: Dropped vs Kept")
    lines.append("")
    dropped_ra = dropped["ratingAvg"].drop_nulls().to_numpy()
    kept_ra = kept["ratingAvg"].drop_nulls().to_numpy()
    if len(dropped_ra) > 0:
        dropped_ra_stats = quantile_summary(dropped_ra)
        kept_ra_stats = quantile_summary(kept_ra)
        lines.append(
            md_table(
                ["Statistic", "Dropped", "Kept"],
                [[k, format_float(dropped_ra_stats[k]), format_float(kept_ra_stats[k])] for k in dropped_ra_stats],
            )
        )
        lines.append("")
        plot_comparison_histogram(
            out / "dropped_ratingAvg_comparison.png",
            dropped_ra, kept_ra, "Dropped", "Kept",
            "ratingAvg: Dropped vs Kept", "ratingAvg",
        )
        lines.append("![ratingAvg Comparison](dropped_ratingAvg_comparison.png)")
    else:
        lines.append("Dropped movie 중 ratingAvg가 존재하는 영화가 없습니다.")
    lines.append("")

    # --- tagCount comparison ---
    lines.append("### tagCount 분포: Dropped vs Kept")
    lines.append("")
    dropped_tc = dropped["tagCount"].to_numpy()
    kept_tc = kept["tagCount"].to_numpy()
    dropped_tc_stats = quantile_summary(dropped_tc)
    kept_tc_stats = quantile_summary(kept_tc)
    lines.append(
        md_table(
            ["Statistic", "Dropped", "Kept"],
            [[k, format_int(int(round(dropped_tc_stats[k]))), format_int(int(round(kept_tc_stats[k])))] for k in dropped_tc_stats],
        )
    )
    lines.append("")
    zero_tc = int((dropped_tc == 0).sum())
    lines.append(f"Dropped movie 중 tagCount == 0: {format_int(zero_tc)} ({percent(zero_tc, dropped.height)})")
    lines.append("")

    # --- Genre drop rate ---
    lines.append("### 장르별 Drop Rate")
    lines.append("")
    before_genres_exploded = before.select(
        pl.col("movieId"),
        pl.col("genres").map_elements(parse_json_list, return_dtype=pl.List(pl.String)).alias("genre_list"),
    ).explode("genre_list").rename({"genre_list": "genre"}).drop_nulls()

    genre_total = before_genres_exploded.group_by("genre").agg(pl.col("movieId").n_unique().alias("total"))
    dropped_genres_exploded = before_genres_exploded.filter(~pl.col("movieId").is_in(kept_ids))
    genre_dropped = dropped_genres_exploded.group_by("genre").agg(pl.col("movieId").n_unique().alias("dropped"))

    genre_rates = (
        genre_total.join(genre_dropped, on="genre", how="left")
        .with_columns(pl.col("dropped").fill_null(0))
        .with_columns((pl.col("dropped") / pl.col("total") * 100).alias("drop_rate"))
        .sort("total", descending=True)
    )

    top_genre_rates = genre_rates.head(15)
    lines.append(
        md_table(
            ["Genre", "Total", "Dropped", "Drop Rate"],
            [
                [row["genre"], format_int(row["total"]), format_int(row["dropped"]), f"{row['drop_rate']:.2f}%"]
                for row in top_genre_rates.iter_rows(named=True)
            ],
        )
    )
    lines.append("")

    plot_bar(
        out / "dropped_genre_drop_rate.png",
        top_genre_rates["genre"].to_list(),
        top_genre_rates["drop_rate"].to_list(),
        "Genre Drop Rate (%)",
        "Genre",
        "Drop Rate (%)",
    )
    lines.append("![Genre Drop Rate](dropped_genre_drop_rate.png)")
    lines.append("")

    # --- Issue combination detail ---
    issue_combos = drop_report.get("issue_combinations", [])
    if issue_combos:
        single = sum(c["count"] for c in issue_combos if len(c["issues"]) == 1)
        multi = sum(c["count"] for c in issue_combos if len(c["issues"]) > 1)
        total_dropped = single + multi
        lines.append("### Issue 단일 vs 복합 사유")
        lines.append("")
        lines.append(
            md_table(
                ["Type", "Count", "Share"],
                [
                    ["단일 사유", format_int(single), percent(single, total_dropped)],
                    ["복합 사유 (2개 이상)", format_int(multi), percent(multi, total_dropped)],
                ],
            )
        )
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Section 4: Final Rating Data Analysis
# ---------------------------------------------------------------------------


def build_ratings_aggregates(ratings_lf: pl.LazyFrame) -> dict[str, pl.DataFrame]:
    ratings_summary_lf = ratings_lf.select(
        pl.len().alias("ratings_rows"),
        pl.col("userId").n_unique().alias("unique_users"),
        pl.col("movieId").n_unique().alias("unique_movies"),
        pl.col("rating").mean().alias("mean_rating"),
        pl.col("rating").median().alias("median_rating"),
        pl.col("ratedAt").min().alias("first_rated_at"),
        pl.col("ratedAt").max().alias("last_rated_at"),
    )
    rating_value_counts_lf = ratings_lf.group_by("rating").agg(pl.len().alias("count")).sort("rating")
    user_stats_lf = (
        ratings_lf.group_by("userId")
        .agg(
            pl.len().alias("ratingCount"),
            pl.col("ratedAt").min().alias("firstRatedAt"),
            pl.col("ratedAt").max().alias("lastRatedAt"),
        )
        .sort("userId")
    )
    movie_stats_lf = ratings_lf.group_by("movieId").agg(pl.len().alias("ratingCount")).sort("movieId")
    ratings_by_year_lf = (
        ratings_lf.with_columns(pl.col("ratedAt").str.slice(0, 4).alias("year"))
        .group_by("year")
        .agg(pl.len().alias("count"))
        .sort("year")
    )

    ratings_summary, rating_value_counts, user_stats, movie_stats, ratings_by_year = pl.collect_all(
        [ratings_summary_lf, rating_value_counts_lf, user_stats_lf, movie_stats_lf, ratings_by_year_lf]
    )

    span_days = [
        (parse_iso_utc(last) - parse_iso_utc(first)).total_seconds() / 86400.0
        for first, last in zip(user_stats["firstRatedAt"].to_list(), user_stats["lastRatedAt"].to_list())
    ]
    user_stats = user_stats.with_columns(pl.Series("activeSpanDays", span_days))

    return {
        "ratings_summary": ratings_summary,
        "rating_value_counts": rating_value_counts,
        "user_stats": user_stats,
        "movie_stats": movie_stats,
        "ratings_by_year": ratings_by_year,
    }


def section_final_rating_analysis(
    ratings_summary: dict[str, object],
    rating_value_counts: pl.DataFrame,
    user_stats: pl.DataFrame,
    movie_stats: pl.DataFrame,
    ratings_by_year: pl.DataFrame,
    sequence_report: dict[str, object],
    sequence_line_count: int,
    out: Path,
) -> list[str]:
    lines: list[str] = []
    lines.append("## 3. 최종 Rating 데이터 종합 분석")
    lines.append("")

    # --- Basic profile ---
    lines.append("### 기본 프로파일")
    lines.append("")
    lines.append(
        md_table(
            ["Metric", "Value"],
            [
                ["Total ratings", format_int(ratings_summary["ratings_rows"])],
                ["Unique users", format_int(ratings_summary["unique_users"])],
                ["Unique movies", format_int(ratings_summary["unique_movies"])],
                ["Mean rating", format_float(ratings_summary["mean_rating"])],
                ["Median rating", format_float(ratings_summary["median_rating"])],
                ["First ratedAt", str(ratings_summary["first_rated_at"])],
                ["Last ratedAt", str(ratings_summary["last_rated_at"])],
            ],
        )
    )
    lines.append("")

    # --- Sparsity ---
    lines.append("### Sparsity 분석")
    lines.append("")
    n_users = ratings_summary["unique_users"]
    n_movies = ratings_summary["unique_movies"]
    n_ratings = ratings_summary["ratings_rows"]
    matrix_size = n_users * n_movies
    density = n_ratings / matrix_size if matrix_size > 0 else 0.0
    sparsity = 1.0 - density
    lines.append(
        md_table(
            ["Metric", "Value"],
            [
                ["User-Item matrix size", f"{format_int(n_users)} x {format_int(n_movies)} = {format_int(matrix_size)}"],
                ["Observed interactions", format_int(n_ratings)],
                ["Density", f"{density * 100:.6f}%"],
                ["Sparsity", f"{sparsity * 100:.4f}%"],
            ],
        )
    )
    lines.append("")

    # --- Rating value distribution ---
    lines.append("### Rating 값 분포")
    lines.append("")
    rating_labels = [format_float(float(v), 1) for v in rating_value_counts["rating"].to_list()]
    rating_counts = [int(v) for v in rating_value_counts["count"].to_list()]
    lines.append(
        md_table(
            ["Rating", "Count", "Share"],
            [[label, format_int(c), percent(c, n_ratings)] for label, c in zip(rating_labels, rating_counts)],
        )
    )
    lines.append("")
    plot_bar(
        out / "rating_value_distribution.png", rating_labels, [float(v) for v in rating_counts],
        "Rating Value Distribution", "Rating", "Count",
    )
    lines.append("![Rating Value Distribution](rating_value_distribution.png)")
    lines.append("")

    # --- Temporal analysis ---
    lines.append("### 연도별 Rating 트렌드")
    lines.append("")
    years = ratings_by_year["year"].to_list()
    year_counts = ratings_by_year["count"].to_list()
    cumsum = np.cumsum(year_counts)
    cum_pct = (cumsum / cumsum[-1] * 100).tolist() if len(cumsum) > 0 else []

    lines.append(
        md_table(
            ["Year", "Ratings", "Cumulative %"],
            [[y, format_int(c), f"{p:.1f}%"] for y, c, p in zip(years, year_counts, cum_pct)],
        )
    )
    lines.append("")
    plot_dual_line(
        out / "ratings_by_year.png", years, [float(c) for c in year_counts], cum_pct,
        "Ratings by Year", "Year", "Rating Count", "Cumulative %",
    )
    lines.append("![Ratings by Year](ratings_by_year.png)")
    lines.append("")

    # --- Interaction Density ---
    lines.append("### Interaction Density")
    lines.append("")
    ratings_per_user = user_stats["ratingCount"].to_numpy()
    ratings_per_movie = movie_stats["ratingCount"].to_numpy()

    lines.append("#### User당 rating 수")
    lines.append("")
    user_q = quantile_summary(ratings_per_user)
    lines.append(md_quantile_table("Ratings per user", user_q, "int"))
    lines.append("")
    plot_histogram(out / "ratings_per_user_distribution.png", ratings_per_user, "Ratings per User (log scale)", "ratingsPerUser", log_scale=True)
    lines.append("![Ratings per User](ratings_per_user_distribution.png)")
    lines.append("")

    lines.append("#### Movie당 rating 수")
    lines.append("")
    movie_q = quantile_summary(ratings_per_movie)
    lines.append(md_quantile_table("Ratings per movie", movie_q, "int"))
    lines.append("")
    plot_histogram(out / "ratings_per_movie_distribution.png", ratings_per_movie, "Ratings per Movie (log scale)", "ratingsPerMovie", log_scale=True)
    lines.append("![Ratings per Movie](ratings_per_movie_distribution.png)")
    lines.append("")

    # --- Long-tail / Popularity Bias ---
    lines.append("### Long-tail & Popularity Bias")
    lines.append("")

    # Movie threshold coverage
    movie_thresholds = [1, 5, 10, 20, 50, 100, 500, 1000]
    movie_cov = threshold_coverage(ratings_per_movie, movie_thresholds)
    lines.append("#### Movie Threshold Coverage")
    lines.append("")
    lines.append(
        md_table(
            ["Min ratings", "Movies", "Movies %", "Interactions", "Interactions %"],
            [
                [
                    f">= {c['threshold']}",
                    format_int(c["items"]),
                    f"{c['items_pct'] * 100:.2f}%",
                    format_int(c["interactions"]),
                    f"{c['interactions_pct'] * 100:.2f}%",
                ]
                for c in movie_cov
            ],
        )
    )
    lines.append("")
    plot_threshold_coverage(
        out / "movie_threshold_coverage.png",
        movie_thresholds,
        [c["items"] for c in movie_cov],
        [c["interactions_pct"] for c in movie_cov],
        "Movie Threshold Coverage",
        "Min ratings per movie", "Movies", "Interaction Coverage %",
    )
    lines.append("![Movie Threshold Coverage](movie_threshold_coverage.png)")
    lines.append("")

    # User threshold coverage
    user_thresholds = [20, 50, 100, 200, 500, 1000]
    user_cov = threshold_coverage(ratings_per_user, user_thresholds)
    lines.append("#### User Threshold Coverage")
    lines.append("")
    lines.append(
        md_table(
            ["Min ratings", "Users", "Users %", "Interactions", "Interactions %"],
            [
                [
                    f">= {c['threshold']}",
                    format_int(c["items"]),
                    f"{c['items_pct'] * 100:.2f}%",
                    format_int(c["interactions"]),
                    f"{c['interactions_pct'] * 100:.2f}%",
                ]
                for c in user_cov
            ],
        )
    )
    lines.append("")
    plot_threshold_coverage(
        out / "user_threshold_coverage.png",
        user_thresholds,
        [c["items"] for c in user_cov],
        [c["interactions_pct"] for c in user_cov],
        "User Threshold Coverage",
        "Min ratings per user", "Users", "Interaction Coverage %",
    )
    lines.append("![User Threshold Coverage](user_threshold_coverage.png)")
    lines.append("")

    # Concentration
    movie_sorted = np.sort(ratings_per_movie)[::-1]
    user_sorted = np.sort(ratings_per_user)[::-1]
    movie_conc = concentration_stats(movie_sorted)
    user_conc = concentration_stats(user_sorted)

    lines.append("#### Concentration (Head Dominance)")
    lines.append("")
    lines.append(
        md_table(
            ["Fraction", "Movie: top N", "Movie: interaction share", "User: top N", "User: interaction share"],
            [
                [
                    f"Top {int(mc['fraction'] * 100)}%",
                    format_int(mc["top_n"]),
                    f"{mc['share'] * 100:.2f}%",
                    format_int(uc["top_n"]),
                    f"{uc['share'] * 100:.2f}%",
                ]
                for mc, uc in zip(movie_conc, user_conc)
            ],
        )
    )
    lines.append("")

    # Gini
    movie_gini = gini_coefficient(ratings_per_movie)
    user_gini = gini_coefficient(ratings_per_user)
    lines.append("#### Gini Coefficient")
    lines.append("")
    lines.append(
        md_table(
            ["Dimension", "Gini"],
            [
                ["Movie (rating count)", format_float(movie_gini, 4)],
                ["User (rating count)", format_float(user_gini, 4)],
            ],
        )
    )
    lines.append("")
    lines.append("(0 = 완전 균등, 1 = 완전 집중. 추천 시스템에서는 0.8 이상이면 극심한 long-tail)")
    lines.append("")

    # --- Cold-start profile ---
    lines.append("### Cold-start 프로파일")
    lines.append("")

    lines.append("#### Movie Cold-start")
    lines.append("")
    movie_cold_thresholds = [5, 10, 20]
    cold_movie_rows = []
    for t in movie_cold_thresholds:
        count = int((ratings_per_movie < t).sum())
        cold_movie_rows.append([f"< {t} ratings", format_int(count), percent(count, n_movies)])
    lines.append(md_table(["Condition", "Movies", "Share"], cold_movie_rows))
    lines.append("")

    lines.append("#### User Cold-start")
    lines.append("")
    user_cold_thresholds = [20, 50]
    cold_user_rows = []
    for t in user_cold_thresholds:
        count = int((ratings_per_user < t).sum())
        cold_user_rows.append([f"< {t} ratings", format_int(count), percent(count, n_users)])
    lines.append(md_table(["Condition", "Users", "Share"], cold_user_rows))
    lines.append("")

    # --- User Sequence Analysis ---
    lines.append("### User Sequence 분석")
    lines.append("")

    sequence_summary = sequence_report["summary"]
    lines.append("#### Sequence Output Summary")
    lines.append("")
    lines.append(
        md_table(
            ["Metric", "Value"],
            [
                ["JSONL lines", format_int(sequence_line_count)],
                ["users_output", format_int(sequence_summary["users_output"])],
                ["rating_count_sum", format_int(sequence_summary["rating_count_sum"])],
                ["invalid_rows", format_int(sequence_summary["invalid_rows"])],
                ["Min ratingCount/user", format_int(sequence_summary["min_rating_count_per_user"])],
                ["Max ratingCount/user", format_int(sequence_summary["max_rating_count_per_user"])],
            ],
        )
    )
    lines.append("")
    lines.append(
        f"Line count == users_output: {'OK' if sequence_line_count == sequence_summary['users_output'] else 'MISMATCH'}"
    )
    lines.append("")

    # Sequence length distribution (== ratings per user)
    lines.append("#### Sequence 길이 분포")
    lines.append("")
    lines.append(md_quantile_table("Sequence length", user_q, "int"))
    lines.append("")
    plot_histogram(
        out / "sequence_length_distribution.png", ratings_per_user,
        "Sequence Length Distribution (log scale)", "sequenceLength", log_scale=True,
    )
    lines.append("![Sequence Length](sequence_length_distribution.png)")
    lines.append("")

    # Activity span
    lines.append("#### Activity Span 분포")
    lines.append("")
    span_days_arr = user_stats["activeSpanDays"].to_numpy()
    span_q = quantile_summary(span_days_arr)
    lines.append(md_quantile_table("Active span (days)", span_q, "float"))
    lines.append("")
    plot_histogram(
        out / "user_activity_span_distribution.png", span_days_arr,
        "User Activity Span (days)", "activeSpanDays", log_scale=False,
    )
    lines.append("![Activity Span](user_activity_span_distribution.png)")
    lines.append("")

    # Span buckets
    lines.append("#### Activity Span Buckets")
    lines.append("")
    buckets = span_bucket_stats(span_days_arr)
    lines.append(
        md_table(
            ["Bucket", "Users", "Share"],
            [[b["label"], format_int(b["count"]), f"{b['pct'] * 100:.2f}%"] for b in buckets],
        )
    )
    lines.append("")
    plot_span_buckets(out / "user_span_buckets.png", buckets, "User Activity Span Buckets")
    lines.append("![Span Buckets](user_span_buckets.png)")
    lines.append("")

    # Longest history / span
    longest_history = user_stats.sort("ratingCount", descending=True).head(1).row(0, named=True)
    longest_span = user_stats.sort("activeSpanDays", descending=True).head(1).row(0, named=True)
    lines.append("#### Notable Users")
    lines.append("")
    lines.append(
        md_table(
            ["Observation", "Value"],
            [
                ["Longest history userId", format_int(longest_history["userId"])],
                ["Longest history ratingCount", format_int(longest_history["ratingCount"])],
                ["Longest active-span userId", format_int(longest_span["userId"])],
                ["Longest active-span days", format_float(longest_span["activeSpanDays"])],
            ],
        )
    )
    lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    out = ensure_dir(OUTPUT_DIR)
    ensure_dir(MPLCONFIG_DIR)

    print("[load] Loading datasets...", flush=True)
    movies_before = load_movies_csv(DEFAULT_MOVIES_BEFORE_PATH)
    movies_after = load_movies_csv(DEFAULT_MOVIES_AFTER_PATH)
    ratings_lf = scan_ratings_csv(DEFAULT_RATINGS_PATH)
    movie_drop_report = load_json(DEFAULT_MOVIE_DROP_REPORT_PATH)
    sequence_report = load_json(DEFAULT_SEQUENCE_REPORT_PATH)

    print("[agg] Building rating aggregates...", flush=True)
    agg = build_ratings_aggregates(ratings_lf)
    ratings_summary = agg["ratings_summary"].row(0, named=True)
    rating_value_counts = agg["rating_value_counts"]
    user_stats = agg["user_stats"]
    movie_stats = agg["movie_stats"]
    ratings_by_year = agg["ratings_by_year"]
    sequence_line_count = count_lines(DEFAULT_RATINGS_SEQUENCE_PATH)

    lines: list[str] = []
    lines.append("# Processed Data EDA Report")
    lines.append("")
    lines.append(f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append("")

    print("[1/3] Section 1: Movie Drop Comparison...", flush=True)
    lines.extend(section_movie_drop_comparison(movies_before, movies_after, movie_drop_report, out))

    print("[2/3] Section 2: Dropped Movie Deep Dive...", flush=True)
    lines.extend(section_dropped_movie_deep_dive(movies_before, movies_after, movie_drop_report, out))

    print("[3/3] Section 3: Final Rating Analysis...", flush=True)
    lines.extend(
        section_final_rating_analysis(
            ratings_summary, rating_value_counts, user_stats, movie_stats,
            ratings_by_year, sequence_report, sequence_line_count, out,
        )
    )

    report_path = out / "eda_report.md"
    with report_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[done] EDA report: {report_path}", flush=True)
    print(f"[done] Charts: {out}/", flush=True)


if __name__ == "__main__":
    main()
