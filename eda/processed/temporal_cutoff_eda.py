#!/usr/bin/env python3
"""Focused EDA for choosing a temporal cutoff T.

This script does not create train/stream splits. It profiles candidate cutoff
times so the later temporal training and replay pipeline can choose T with
clear volume, cold-start, and streaming pressure tradeoffs.
"""

from __future__ import annotations

import argparse
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
DEFAULT_RATINGS_PATH = REPO_ROOT / "data" / "ratings_drop.csv"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR
DEFAULT_QUANTILES = "0.70,0.80,0.85,0.90,0.95"
DEFAULT_CALENDAR_YEARS = "2018,2019,2020,2021,2022"
DEFAULT_RECENT_YEARS = "1,2,3,5"


@dataclass(frozen=True)
class CutoffCandidate:
    candidate_id: str
    kind: str
    label: str
    cutoff: str
    cutoff_date: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ratings", type=Path, default=DEFAULT_RATINGS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--quantiles", default=DEFAULT_QUANTILES)
    parser.add_argument("--calendar-years", default=DEFAULT_CALENDAR_YEARS)
    parser.add_argument("--recent-years", default=DEFAULT_RECENT_YEARS)
    parser.add_argument("--positive-rating-threshold", type=float, default=4.0)
    parser.add_argument("--refit-min-events", type=int, default=3)
    parser.add_argument("--assign-trigger-count", type=int, default=3)
    parser.add_argument("--outlier-trigger-count", type=int, default=3)
    return parser.parse_args()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_float_list(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_iso_utc(value: str) -> datetime:
    return datetime.strptime(value, RATED_AT_FORMAT).replace(tzinfo=timezone.utc)


def day_end(day: str) -> str:
    return f"{day}T23:59:59Z"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


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


def quantile_stats(values: np.ndarray) -> dict[str, float]:
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {
            "p50": float("nan"),
            "p90": float("nan"),
            "p95": float("nan"),
            "p99": float("nan"),
            "max": float("nan"),
            "mean": float("nan"),
        }
    return {
        "p50": float(np.percentile(values, 50)),
        "p90": float(np.percentile(values, 90)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
    }


def scan_ratings(path: Path) -> pl.LazyFrame:
    return pl.scan_csv(
        path,
        schema_overrides={
            "userId": pl.Int64,
            "movieId": pl.Int64,
            "rating": pl.Float64,
            "ratedAt": pl.String,
        },
    )


def collect_base_aggregates(ratings_lf: pl.LazyFrame) -> dict[str, pl.DataFrame]:
    ratings_with_time = ratings_lf.with_columns(
        pl.col("ratedAt").str.slice(0, 4).alias("year"),
        pl.col("ratedAt").str.slice(0, 7).alias("month"),
        pl.col("ratedAt").str.slice(0, 10).alias("day"),
        pl.col("ratedAt").str.slice(0, 13).alias("hour"),
    )
    summary_lf = ratings_lf.select(
        pl.len().alias("total_events"),
        pl.col("userId").n_unique().alias("unique_users"),
        pl.col("movieId").n_unique().alias("unique_movies"),
        pl.col("rating").mean().alias("mean_rating"),
        pl.col("ratedAt").min().alias("first_rated_at"),
        pl.col("ratedAt").max().alias("last_rated_at"),
    )
    by_year_lf = ratings_with_time.group_by("year").agg(pl.len().alias("events")).sort("year")
    by_month_lf = ratings_with_time.group_by("month").agg(pl.len().alias("events")).sort("month")
    by_day_lf = ratings_with_time.group_by("day").agg(pl.len().alias("events")).sort("day")
    by_hour_lf = ratings_with_time.group_by("hour").agg(pl.len().alias("events")).sort("hour")
    user_stats_lf = (
        ratings_lf.group_by("userId")
        .agg(
            pl.len().alias("total_events"),
            pl.col("ratedAt").min().alias("first_rated_at"),
            pl.col("ratedAt").max().alias("last_rated_at"),
        )
        .sort("userId")
    )
    movie_stats_lf = (
        ratings_lf.group_by("movieId")
        .agg(
            pl.len().alias("total_events"),
            pl.col("ratedAt").min().alias("first_rated_at"),
            pl.col("ratedAt").max().alias("last_rated_at"),
        )
        .sort("movieId")
    )
    summary, by_year, by_month, by_day, by_hour, user_stats, movie_stats = pl.collect_all(
        [summary_lf, by_year_lf, by_month_lf, by_day_lf, by_hour_lf, user_stats_lf, movie_stats_lf]
    )
    return {
        "summary": summary,
        "by_year": by_year,
        "by_month": by_month,
        "by_day": by_day,
        "by_hour": by_hour,
        "user_stats": user_stats,
        "movie_stats": movie_stats,
    }


def build_candidates(
    *,
    by_day: pl.DataFrame,
    total_events: int,
    first_rated_at: str,
    last_rated_at: str,
    quantiles: list[float],
    calendar_years: list[int],
    recent_years: list[int],
) -> list[CutoffCandidate]:
    days = by_day["day"].to_list()
    counts = by_day["events"].to_numpy()
    cumulative = np.cumsum(counts)
    first_day = first_rated_at[:10]
    last_day = last_rated_at[:10]
    last_dt = parse_iso_utc(last_rated_at)

    candidates: list[CutoffCandidate] = []
    seen_cutoffs: set[str] = set()

    def add(candidate_id: str, kind: str, label: str, day: str) -> None:
        if day < first_day or day >= last_day:
            return
        cutoff = day_end(day)
        if cutoff in seen_cutoffs:
            return
        seen_cutoffs.add(cutoff)
        candidates.append(CutoffCandidate(candidate_id, kind, label, cutoff, day))

    for q in quantiles:
        target = int(math.ceil(total_events * q))
        idx = int(np.searchsorted(cumulative, target, side="left"))
        idx = min(max(idx, 0), len(days) - 1)
        pct_label = f"q{int(round(q * 100)):02d}"
        add(pct_label, "event_quantile", f"{q:.0%} cumulative events", days[idx])

    for year in calendar_years:
        add(f"year_end_{year}", "calendar_year_end", f"{year} year end", f"{year}-12-31")

    for years in recent_years:
        day = (last_dt - timedelta(days=365 * years)).date().isoformat()
        add(f"recent_{years}y", "recent_window", f"last {years}y stream window", day)

    return sorted(candidates, key=lambda item: item.cutoff)


def collect_user_cutoff_counts(
    ratings_lf: pl.LazyFrame,
    candidates: list[CutoffCandidate],
    *,
    positive_rating_threshold: float,
) -> pl.DataFrame:
    expressions: list[pl.Expr] = [pl.len().alias("total_events")]
    for candidate in candidates:
        post_cond = pl.col("ratedAt") > candidate.cutoff
        post_positive_cond = post_cond & (pl.col("rating") >= positive_rating_threshold)
        expressions.append(post_cond.cast(pl.Int64).sum().alias(f"{candidate.candidate_id}__post_events"))
        expressions.append(
            post_positive_cond.cast(pl.Int64).sum().alias(f"{candidate.candidate_id}__post_positive_proxy")
        )
    return ratings_lf.group_by("userId").agg(expressions).sort("userId").collect()


def values_after_cutoff(frame: pl.DataFrame, key_column: str, value_column: str, cutoff_date: str) -> np.ndarray:
    return frame.filter(pl.col(key_column).str.slice(0, 10) > cutoff_date)[value_column].to_numpy()


def sum_events_through_day(by_day: pl.DataFrame, cutoff_date: str) -> int:
    days = by_day["day"].to_list()
    counts = by_day["events"].to_numpy()
    cumulative = np.cumsum(counts)
    idx = int(np.searchsorted(days, cutoff_date, side="right")) - 1
    if idx < 0:
        return 0
    return int(cumulative[idx])


def choose_recommendation_roles(rows: list[dict[str, Any]]) -> dict[str, str]:
    eligible = [
        row for row in rows
        if row["pre_event_share"] >= 0.70
        and row["post_events"] >= 1_000_000
        and row["post_calendar_days"] >= 180
        and row["post_unique_users"] >= 10_000
    ]
    roles: dict[str, str] = {}
    if not eligible:
        return roles

    balanced = min(eligible, key=lambda row: abs(row["pre_event_share"] - 0.85))
    roles[balanced["candidate_id"]] = "balanced"

    conservative_pool = [row for row in eligible if row["pre_event_share"] >= 0.85]
    if conservative_pool:
        conservative = min(conservative_pool, key=lambda row: abs(row["pre_event_share"] - 0.90))
        roles[conservative["candidate_id"]] = (
            roles.get(conservative["candidate_id"], "") + ",conservative"
        ).strip(",")

    stress_pool = [row for row in eligible if row["pre_event_share"] >= 0.75]
    if stress_pool:
        stress = max(stress_pool, key=lambda row: row["post_hour_events_p99"])
        roles[stress["candidate_id"]] = (roles.get(stress["candidate_id"], "") + ",stress").strip(",")

    return roles


def build_candidate_metrics(
    *,
    candidates: list[CutoffCandidate],
    summary: dict[str, Any],
    by_day: pl.DataFrame,
    by_hour: pl.DataFrame,
    user_stats: pl.DataFrame,
    movie_stats: pl.DataFrame,
    user_cutoff_counts: pl.DataFrame,
    refit_min_events: int,
    assign_trigger_count: int,
    outlier_trigger_count: int,
) -> list[dict[str, Any]]:
    total_events = int(summary["total_events"])
    last_dt = parse_iso_utc(str(summary["last_rated_at"]))
    user_total = user_cutoff_counts["total_events"].to_numpy()
    user_first = user_stats["first_rated_at"].to_numpy()
    movie_first = movie_stats["first_rated_at"].to_numpy()
    movie_last = movie_stats["last_rated_at"].to_numpy()
    movie_total = movie_stats["total_events"].to_numpy()

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        post_event_col = f"{candidate.candidate_id}__post_events"
        post_positive_col = f"{candidate.candidate_id}__post_positive_proxy"
        post_user_counts = user_cutoff_counts[post_event_col].to_numpy()
        post_positive_counts = user_cutoff_counts[post_positive_col].to_numpy()
        pre_user_counts = user_total - post_user_counts
        stream_user_mask = post_user_counts > 0
        train_user_mask = pre_user_counts > 0
        new_user_mask = stream_user_mask & (user_first > candidate.cutoff)
        existing_stream_mask = stream_user_mask & train_user_mask

        pre_events = sum_events_through_day(by_day, candidate.cutoff_date)
        post_events = total_events - pre_events
        post_days_values = values_after_cutoff(by_day, "day", "events", candidate.cutoff_date)
        post_hours_values = values_after_cutoff(by_hour, "hour", "events", candidate.cutoff_date)
        day_stats = quantile_stats(post_days_values.astype(float))
        hour_stats = quantile_stats(post_hours_values.astype(float))

        cutoff_dt = parse_iso_utc(candidate.cutoff)
        post_calendar_days = max((last_dt.date() - cutoff_dt.date()).days, 0)
        post_active_days = int(len(post_days_values))
        post_active_hours = int(len(post_hours_values))

        post_counts_nonzero = post_user_counts[stream_user_mask].astype(float)
        pre_counts_nonzero = pre_user_counts[train_user_mask].astype(float)
        post_user_stats = quantile_stats(post_counts_nonzero)
        pre_user_stats = quantile_stats(pre_counts_nonzero)

        pre_movie_mask = movie_first <= candidate.cutoff
        post_movie_mask = movie_last > candidate.cutoff
        cold_movie_mask = movie_first > candidate.cutoff
        unknown_item_events = int(movie_total[cold_movie_mask].sum())
        post_positive_total = int(post_positive_counts.sum())

        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "kind": candidate.kind,
                "label": candidate.label,
                "cutoff": candidate.cutoff,
                "pre_events": int(pre_events),
                "post_events": int(post_events),
                "pre_event_share": pre_events / total_events if total_events else 0.0,
                "post_event_share": post_events / total_events if total_events else 0.0,
                "post_calendar_days": int(post_calendar_days),
                "post_active_days": post_active_days,
                "post_active_hours": post_active_hours,
                "post_events_per_calendar_day": post_events / post_calendar_days if post_calendar_days else 0.0,
                "post_events_per_active_day": post_events / post_active_days if post_active_days else 0.0,
                "post_events_per_active_hour": post_events / post_active_hours if post_active_hours else 0.0,
                "post_day_events_p50": day_stats["p50"],
                "post_day_events_p90": day_stats["p90"],
                "post_day_events_p95": day_stats["p95"],
                "post_day_events_p99": day_stats["p99"],
                "post_day_events_max": day_stats["max"],
                "post_hour_events_p50": hour_stats["p50"],
                "post_hour_events_p90": hour_stats["p90"],
                "post_hour_events_p95": hour_stats["p95"],
                "post_hour_events_p99": hour_stats["p99"],
                "post_hour_events_max": hour_stats["max"],
                "pre_unique_users": int(train_user_mask.sum()),
                "post_unique_users": int(stream_user_mask.sum()),
                "new_stream_users": int(new_user_mask.sum()),
                "existing_stream_users": int(existing_stream_mask.sum()),
                "new_stream_user_share": (
                    float(new_user_mask.sum()) / float(stream_user_mask.sum()) if stream_user_mask.sum() else 0.0
                ),
                "pre_user_events_p50": pre_user_stats["p50"],
                "pre_user_events_p90": pre_user_stats["p90"],
                "pre_user_events_p99": pre_user_stats["p99"],
                "post_user_events_p50": post_user_stats["p50"],
                "post_user_events_p90": post_user_stats["p90"],
                "post_user_events_p99": post_user_stats["p99"],
                "post_user_events_max": post_user_stats["max"],
                "pre_unique_movies": int(pre_movie_mask.sum()),
                "post_unique_movies": int(post_movie_mask.sum()),
                "cold_stream_movies": int(cold_movie_mask.sum()),
                "unknown_item_events": unknown_item_events,
                "unknown_item_event_share": unknown_item_events / post_events if post_events else 0.0,
                "post_positive_proxy_events": post_positive_total,
                "post_positive_proxy_event_share": post_positive_total / post_events if post_events else 0.0,
                "users_with_post_events_ge_refit_min": int((post_user_counts >= refit_min_events).sum()),
                "users_with_positive_proxy_ge_refit_min": int((post_positive_counts >= refit_min_events).sum()),
                "users_with_post_events_ge_assign_trigger": int((post_user_counts >= assign_trigger_count).sum()),
                "users_with_post_events_ge_outlier_trigger": int((post_user_counts >= outlier_trigger_count).sum()),
            }
        )

    roles = choose_recommendation_roles(rows)
    for row in rows:
        row["recommendation_role"] = roles.get(row["candidate_id"], "")
    return rows


def plot_yearly_counts(path: Path, by_year: pl.DataFrame) -> None:
    years = by_year["year"].to_list()
    events = by_year["events"].to_numpy() / 1_000_000
    plt.figure(figsize=(12, 5))
    plt.bar(years, events, color="#2A9D8F")
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Rating events (millions)")
    plt.title("Rating Events by Year")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_monthly_counts(path: Path, by_month: pl.DataFrame) -> None:
    months = by_month["month"].to_list()
    events = by_month["events"].to_numpy()
    x = np.arange(len(months))
    plt.figure(figsize=(14, 5))
    plt.plot(x, events, color="#264653", linewidth=1.5)
    tick_step = max(1, len(months) // 18)
    plt.xticks(x[::tick_step], months[::tick_step], rotation=45, ha="right")
    plt.ylabel("Rating events")
    plt.title("Rating Events by Month")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_candidate_volume(path: Path, rows: list[dict[str, Any]]) -> None:
    labels = [row["candidate_id"] for row in rows]
    pre = np.array([row["pre_events"] for row in rows]) / 1_000_000
    post = np.array([row["post_events"] for row in rows]) / 1_000_000
    x = np.arange(len(labels))
    plt.figure(figsize=(13, 5))
    plt.bar(x, pre, label="pre-T train", color="#2A9D8F")
    plt.bar(x, post, bottom=pre, label="post-T stream", color="#E76F51")
    plt.xticks(x, labels, rotation=45, ha="right")
    plt.ylabel("Rating events (millions)")
    plt.title("Candidate Cutoff Train vs Stream Volume")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_candidate_pressure(path: Path, rows: list[dict[str, Any]], column: str, ylabel: str, title: str) -> None:
    labels = [row["candidate_id"] for row in rows]
    values = [row[column] for row in rows]
    x = np.arange(len(labels))
    plt.figure(figsize=(13, 5))
    plt.bar(x, values, color="#F4A261")
    plt.xticks(x, labels, rotation=45, ha="right")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def write_report(
    *,
    path: Path,
    args: argparse.Namespace,
    summary: dict[str, Any],
    by_year: pl.DataFrame,
    rows: list[dict[str, Any]],
) -> None:
    generated_at = datetime.now(timezone.utc).strftime(RATED_AT_FORMAT)
    recommended = [row for row in rows if row["recommendation_role"]]
    lines: list[str] = []
    lines.append("# Temporal Cutoff T Candidate EDA")
    lines.append("")
    lines.append(f"Generated at: {generated_at}")
    lines.append("")
    lines.append("## 목적")
    lines.append("")
    lines.append(
        "`T` 이전 데이터로 offline model/state를 만들고 `T` 이후 데이터를 streaming replay로 흘릴 때, "
        "어느 cutoff가 학습량과 stream 부하 관찰량의 균형이 좋은지 판단하기 위한 EDA다."
    )
    lines.append("")
    lines.append("## 입력")
    lines.append("")
    lines.append(
        md_table(
            ["Item", "Value"],
            [
                ["ratings", str(args.ratings.relative_to(REPO_ROOT) if args.ratings.is_relative_to(REPO_ROOT) else args.ratings)],
                ["positive proxy", f"rating >= {args.positive_rating_threshold:g}"],
                ["refit_min_events", str(args.refit_min_events)],
                ["assign_trigger_count", str(args.assign_trigger_count)],
                ["outlier_trigger_count", str(args.outlier_trigger_count)],
            ],
        )
    )
    lines.append("")
    lines.append("## 전체 프로파일")
    lines.append("")
    lines.append(
        md_table(
            ["Metric", "Value"],
            [
                ["Total rating events", format_int(summary["total_events"])],
                ["Unique users", format_int(summary["unique_users"])],
                ["Unique movies", format_int(summary["unique_movies"])],
                ["Mean rating", format_float(summary["mean_rating"])],
                ["First ratedAt", str(summary["first_rated_at"])],
                ["Last ratedAt", str(summary["last_rated_at"])],
            ],
        )
    )
    lines.append("")
    lines.append("## 연도별 이벤트")
    lines.append("")
    cumulative = np.cumsum(by_year["events"].to_numpy())
    total = int(summary["total_events"])
    lines.append(
        md_table(
            ["Year", "Events", "Cumulative"],
            [
                [year, format_int(events), percent(cum, total)]
                for year, events, cum in zip(by_year["year"].to_list(), by_year["events"].to_list(), cumulative)
            ],
        )
    )
    lines.append("")
    lines.append("![Rating Events by Year](temporal_cutoff_events_by_year.png)")
    lines.append("")
    lines.append("![Rating Events by Month](temporal_cutoff_events_by_month.png)")
    lines.append("")
    lines.append("## 후보별 핵심 비교")
    lines.append("")
    lines.append(
        md_table(
            [
                "Candidate",
                "Cutoff T",
                "Pre events",
                "Post events",
                "Post days",
                "Avg/day",
                "Hour p99",
                "Post users",
                "New users",
                "Unknown item events",
                "Positive proxy",
                "Role",
            ],
            [
                [
                    row["candidate_id"],
                    row["cutoff"],
                    f"{format_int(row['pre_events'])} ({format_pct(row['pre_event_share'])})",
                    f"{format_int(row['post_events'])} ({format_pct(row['post_event_share'])})",
                    format_int(row["post_calendar_days"]),
                    format_int(row["post_events_per_calendar_day"]),
                    format_int(row["post_hour_events_p99"]),
                    format_int(row["post_unique_users"]),
                    f"{format_int(row['new_stream_users'])} ({format_pct(row['new_stream_user_share'])})",
                    f"{format_int(row['unknown_item_events'])} ({format_pct(row['unknown_item_event_share'])})",
                    f"{format_int(row['post_positive_proxy_events'])} ({format_pct(row['post_positive_proxy_event_share'])})",
                    row["recommendation_role"] or "",
                ]
                for row in rows
            ],
        )
    )
    lines.append("")
    lines.append("![Candidate Volume](temporal_cutoff_candidate_volume.png)")
    lines.append("")
    lines.append("![Candidate Unknown Item Pressure](temporal_cutoff_unknown_item_pressure.png)")
    lines.append("")
    lines.append("![Candidate Hourly Burst](temporal_cutoff_hourly_burst_p99.png)")
    lines.append("")
    lines.append("## 추천 후보")
    lines.append("")
    if recommended:
        lines.append(
            md_table(
                ["Role", "Candidate", "Cutoff T", "Why"],
                [
                    [
                        row["recommendation_role"],
                        row["candidate_id"],
                        row["cutoff"],
                        (
                            f"pre {format_pct(row['pre_event_share'])}, "
                            f"post {format_int(row['post_events'])} events, "
                            f"{format_int(row['post_events_per_calendar_day'])}/day, "
                            f"unknown item {format_pct(row['unknown_item_event_share'])}"
                        ),
                    ]
                    for row in recommended
                ],
            )
        )
    else:
        lines.append(
            "자동 추천 기준을 만족한 후보가 없다. `post_events`, `post_calendar_days`, "
            "`post_unique_users` 기준을 낮추거나 더 이른 cutoff를 검토해야 한다."
        )
    lines.append("")
    lines.append("## 해석 기준")
    lines.append("")
    lines.append("- `positive proxy`는 실제 streaming z-score positive projection이 아니라 `rating >= threshold` 근사다.")
    lines.append("- `unknown item events`는 post-`T` event 중 pre-`T` item vocabulary에 없을 movie event 압력이다.")
    lines.append("- `Hour p99`는 post-`T` active hour 기준 burst proxy다. 실제 replay 부하는 `--speed`와 stage latency에 의해 결정된다.")
    lines.append("- `new users`가 높을수록 pre-`T` user state seed 없이 시작하는 user가 많다.")
    lines.append("")
    lines.append("## 후속 구현 필요 항목")
    lines.append("")
    lines.append("- `model.batch.train` 또는 dataset loader에 `--max-rated-at` 계열 cutoff option 추가")
    lines.append("- canonical extract / batch cluster / state seed가 같은 `T`와 output root를 공유하도록 run layout 정의")
    lines.append("- replay input 생성기에 `--min-rated-at-exclusive` 또는 `--after-rated-at` option 추가")
    lines.append("- SQLite runtime store에서 post-`T` load profile query/report를 뽑는 helper 추가")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_dir = ensure_dir(args.output_dir)
    ratings_lf = scan_ratings(args.ratings)

    aggregates = collect_base_aggregates(ratings_lf)
    summary = aggregates["summary"].to_dicts()[0]
    by_year = aggregates["by_year"]
    by_month = aggregates["by_month"]
    by_day = aggregates["by_day"]
    by_hour = aggregates["by_hour"]
    user_stats = aggregates["user_stats"]
    movie_stats = aggregates["movie_stats"]

    candidates = build_candidates(
        by_day=by_day,
        total_events=int(summary["total_events"]),
        first_rated_at=str(summary["first_rated_at"]),
        last_rated_at=str(summary["last_rated_at"]),
        quantiles=parse_float_list(args.quantiles),
        calendar_years=parse_int_list(args.calendar_years),
        recent_years=parse_int_list(args.recent_years),
    )
    if not candidates:
        raise RuntimeError("No valid cutoff candidates were generated.")

    user_cutoff_counts = collect_user_cutoff_counts(
        ratings_lf,
        candidates,
        positive_rating_threshold=args.positive_rating_threshold,
    )
    rows = build_candidate_metrics(
        candidates=candidates,
        summary=summary,
        by_day=by_day,
        by_hour=by_hour,
        user_stats=user_stats,
        movie_stats=movie_stats,
        user_cutoff_counts=user_cutoff_counts,
        refit_min_events=args.refit_min_events,
        assign_trigger_count=args.assign_trigger_count,
        outlier_trigger_count=args.outlier_trigger_count,
    )

    by_month.write_csv(output_dir / "temporal_monthly_event_counts.csv")
    by_day.write_csv(output_dir / "temporal_daily_event_counts.csv")
    by_hour.write_csv(output_dir / "temporal_hourly_event_counts.csv")
    pl.DataFrame(rows).write_csv(output_dir / "temporal_cutoff_candidates.csv")

    plot_yearly_counts(output_dir / "temporal_cutoff_events_by_year.png", by_year)
    plot_monthly_counts(output_dir / "temporal_cutoff_events_by_month.png", by_month)
    plot_candidate_volume(output_dir / "temporal_cutoff_candidate_volume.png", rows)
    plot_candidate_pressure(
        output_dir / "temporal_cutoff_unknown_item_pressure.png",
        rows,
        "unknown_item_event_share",
        "Unknown item event share",
        "Post-T Unknown Item Pressure",
    )
    plot_candidate_pressure(
        output_dir / "temporal_cutoff_hourly_burst_p99.png",
        rows,
        "post_hour_events_p99",
        "Events per active hour (p99)",
        "Post-T Hourly Burst Proxy",
    )

    write_report(
        path=output_dir / "temporal_cutoff_eda_report.md",
        args=args,
        summary=summary,
        by_year=by_year,
        rows=rows,
    )

    print(f"Wrote report: {output_dir / 'temporal_cutoff_eda_report.md'}")
    print(f"Wrote candidates: {output_dir / 'temporal_cutoff_candidates.csv'}")


if __name__ == "__main__":
    main()
