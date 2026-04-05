from __future__ import annotations

import csv
import os
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np

from .shared import (
    BASE_DIR,
    REPO_ROOT,
    at_most_stats,
    build_user_sequence_arrays,
    concentration_stats,
    dataset_output_dir,
    format_count_stat,
    format_float,
    format_int,
    markdown_table,
    parse_movie_year,
    percent,
    plot_dual_line_chart,
    plot_long_tail_histogram,
    plot_rating_distribution,
    plot_span_buckets,
    plot_threshold_coverage,
    quantile_summary,
    span_bucket_stats,
    span_stats_by_sequence_threshold,
    threshold_rows_from_array,
    timestamp_to_year,
    utc_date_str,
    resolve_existing_dir,
    write_markdown,
)

DATASET_DIR = resolve_existing_dir(
    "MovieLens ml-32m directory",
    [
        os.path.join(REPO_ROOT, "data", "ml-32m", "raw"),
        os.path.join(REPO_ROOT, "data", "ml-32m"),
        os.path.join(BASE_DIR, "ml-32m"),
    ],
)
MOVIES_CSV = os.path.join(DATASET_DIR, "movies.csv")
RATINGS_CSV = os.path.join(DATASET_DIR, "ratings.csv")
TAGS_CSV = os.path.join(DATASET_DIR, "tags.csv")
LINKS_CSV = os.path.join(DATASET_DIR, "links.csv")
OUTPUT_DIR = dataset_output_dir("ml_32m")


def load_movies() -> dict:
    print("[ml32m:movies] loading metadata...", flush=True)
    movie_titles: dict[int, str] = {}
    movie_genres: dict[int, list[str]] = {}
    genre_movie_counts: Counter[str] = Counter()
    duplicate_movie_ids = 0
    titles_missing_year = 0
    no_genre_movies = 0

    with open(MOVIES_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            movie_id = int(row[0])
            title = row[1]
            genres_raw = row[2]

            if movie_id in movie_titles:
                duplicate_movie_ids += 1

            movie_titles[movie_id] = title
            genres = genres_raw.split("|") if genres_raw else []
            movie_genres[movie_id] = genres

            if genres == ["(no genres listed)"]:
                no_genre_movies += 1

            for genre in genres:
                genre_movie_counts[genre] += 1

            if parse_movie_year(title) is None:
                titles_missing_year += 1

    return {
        "movie_titles": movie_titles,
        "movie_genres": movie_genres,
        "genre_movie_counts": genre_movie_counts,
        "duplicate_movie_ids": duplicate_movie_ids,
        "titles_missing_year": titles_missing_year,
        "no_genre_movies": no_genre_movies,
    }


def load_links() -> dict:
    print("[ml32m:links] checking external id coverage...", flush=True)
    total_links = 0
    duplicate_movie_ids = 0
    seen_movie_ids: set[int] = set()
    missing_imdb = 0
    missing_tmdb = 0

    with open(LINKS_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            total_links += 1
            movie_id = int(row[0])
            imdb_id = row[1].strip()
            tmdb_id = row[2].strip()

            if movie_id in seen_movie_ids:
                duplicate_movie_ids += 1
            seen_movie_ids.add(movie_id)

            if not imdb_id:
                missing_imdb += 1
            if not tmdb_id:
                missing_tmdb += 1

    return {
        "total_links": total_links,
        "duplicate_movie_ids": duplicate_movie_ids,
        "missing_imdb": missing_imdb,
        "missing_tmdb": missing_tmdb,
    }


def load_ratings() -> dict:
    print("[ml32m:ratings] streaming ratings.csv...", flush=True)
    ratings_total = 0
    rating_value_counts: Counter[float] = Counter()
    ratings_per_user: defaultdict[int, int] = defaultdict(int)
    min_timestamp_per_user: dict[int, int] = {}
    max_timestamp_per_user: dict[int, int] = {}
    ratings_per_movie: defaultdict[int, int] = defaultdict(int)
    rating_sum_per_movie: defaultdict[int, float] = defaultdict(float)
    ratings_by_year: Counter[int] = Counter()
    min_timestamp: int | None = None
    max_timestamp: int | None = None
    repeated_user_movie_pairs = 0
    prev_pair: tuple[str, str] | None = None

    with open(RATINGS_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            ratings_total += 1
            user_id = int(row[0])
            movie_id = int(row[1])
            rating = float(row[2])
            timestamp = int(row[3])

            rating_value_counts[rating] += 1
            ratings_per_user[user_id] += 1
            if user_id not in min_timestamp_per_user or timestamp < min_timestamp_per_user[user_id]:
                min_timestamp_per_user[user_id] = timestamp
            if user_id not in max_timestamp_per_user or timestamp > max_timestamp_per_user[user_id]:
                max_timestamp_per_user[user_id] = timestamp
            ratings_per_movie[movie_id] += 1
            rating_sum_per_movie[movie_id] += rating
            ratings_by_year[timestamp_to_year(timestamp)] += 1

            if min_timestamp is None or timestamp < min_timestamp:
                min_timestamp = timestamp
            if max_timestamp is None or timestamp > max_timestamp:
                max_timestamp = timestamp

            pair = (row[0], row[1])
            if pair == prev_pair:
                repeated_user_movie_pairs += 1
            prev_pair = pair

            if ratings_total % 5_000_000 == 0:
                print(f"[ml32m:ratings] processed {format_int(ratings_total)} rows...", flush=True)

    return {
        "ratings_total": ratings_total,
        "rating_value_counts": rating_value_counts,
        "ratings_per_user": ratings_per_user,
        "min_timestamp_per_user": min_timestamp_per_user,
        "max_timestamp_per_user": max_timestamp_per_user,
        "ratings_per_movie": ratings_per_movie,
        "rating_sum_per_movie": rating_sum_per_movie,
        "ratings_by_year": ratings_by_year,
        "min_timestamp": min_timestamp,
        "max_timestamp": max_timestamp,
        "repeated_user_movie_pairs": repeated_user_movie_pairs,
    }


def load_tags() -> dict:
    print("[ml32m:tags] streaming tags.csv...", flush=True)
    tags_total = 0
    normalized_tag_counts: Counter[str] = Counter()
    tags_per_user: defaultdict[int, int] = defaultdict(int)
    tags_per_movie: defaultdict[int, int] = defaultdict(int)
    tags_by_year: Counter[int] = Counter()
    blank_tags = 0
    min_timestamp: int | None = None
    max_timestamp: int | None = None

    with open(TAGS_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            tags_total += 1
            user_id = int(row[0])
            movie_id = int(row[1])
            tag = " ".join(row[2].strip().lower().split())
            timestamp = int(row[3])

            if not tag:
                blank_tags += 1
            else:
                normalized_tag_counts[tag] += 1

            tags_per_user[user_id] += 1
            tags_per_movie[movie_id] += 1
            tags_by_year[timestamp_to_year(timestamp)] += 1

            if min_timestamp is None or timestamp < min_timestamp:
                min_timestamp = timestamp
            if max_timestamp is None or timestamp > max_timestamp:
                max_timestamp = timestamp

            if tags_total % 500_000 == 0:
                print(f"[ml32m:tags] processed {format_int(tags_total)} rows...", flush=True)

    return {
        "tags_total": tags_total,
        "normalized_tag_counts": normalized_tag_counts,
        "tags_per_user": tags_per_user,
        "tags_per_movie": tags_per_movie,
        "tags_by_year": tags_by_year,
        "blank_tags": blank_tags,
        "min_timestamp": min_timestamp,
        "max_timestamp": max_timestamp,
    }


def build_genre_rating_summary(
    movie_genres: dict[int, list[str]],
    ratings_per_movie: dict[int, int],
    rating_sum_per_movie: dict[int, float],
) -> list[tuple[str, int, float]]:
    genre_rating_counts: Counter[str] = Counter()
    genre_rating_sums: defaultdict[str, float] = defaultdict(float)

    for movie_id, count in ratings_per_movie.items():
        genres = movie_genres.get(movie_id, [])
        rating_sum = rating_sum_per_movie[movie_id]
        for genre in genres:
            genre_rating_counts[genre] += count
            genre_rating_sums[genre] += rating_sum

    rows = []
    for genre, count in genre_rating_counts.items():
        rows.append((genre, count, genre_rating_sums[genre] / count))
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows


def top_movies_by_count(
    movie_titles: dict[int, str],
    ratings_per_movie: dict[int, int],
    rating_sum_per_movie: dict[int, float],
    limit: int = 10,
) -> list[list[str]]:
    ranked = sorted(ratings_per_movie.items(), key=lambda item: item[1], reverse=True)[:limit]
    rows = []
    for movie_id, count in ranked:
        rows.append(
            [
                str(movie_id),
                movie_titles.get(movie_id, f"movieId={movie_id}"),
                format_int(count),
                format_float(rating_sum_per_movie[movie_id] / count, 3),
            ]
        )
    return rows


def top_movies_by_average(
    movie_titles: dict[int, str],
    ratings_per_movie: dict[int, int],
    rating_sum_per_movie: dict[int, float],
    min_ratings: int,
    limit: int = 10,
) -> list[list[str]]:
    candidates = []
    for movie_id, count in ratings_per_movie.items():
        if count < min_ratings:
            continue
        candidates.append((movie_id, count, rating_sum_per_movie[movie_id] / count))

    ranked = sorted(candidates, key=lambda item: (-item[2], -item[1], item[0]))[:limit]
    return [
        [str(movie_id), movie_titles.get(movie_id, f"movieId={movie_id}"), format_int(count), format_float(avg, 3)]
        for movie_id, count, avg in ranked
    ]


def top_movies_by_tags(movie_titles: dict[int, str], tags_per_movie: dict[int, int], limit: int = 10) -> list[list[str]]:
    ranked = sorted(tags_per_movie.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [[str(movie_id), movie_titles.get(movie_id, f"movieId={movie_id}"), format_int(count)] for movie_id, count in ranked]


def top_users_by_sequence_length(
    ratings_per_user: dict[int, int],
    min_timestamp_per_user: dict[int, int],
    max_timestamp_per_user: dict[int, int],
    limit: int = 10,
) -> list[list[str]]:
    ranked = sorted(ratings_per_user.items(), key=lambda item: (-item[1], item[0]))[:limit]
    rows = []
    for user_id, count in ranked:
        span_days = (max_timestamp_per_user[user_id] - min_timestamp_per_user[user_id]) / 86400
        rows.append(
            [
                str(user_id),
                format_int(count),
                format_float(span_days, 1),
                utc_date_str(min_timestamp_per_user[user_id]),
                utc_date_str(max_timestamp_per_user[user_id]),
            ]
        )
    return rows


def write_eda_report(
    movies_meta: dict,
    links_meta: dict,
    ratings_meta: dict,
    tags_meta: dict,
    genre_rating_summary: list[tuple[str, int, float]],
) -> str:
    report_path = os.path.join(OUTPUT_DIR, "eda_report.md")

    movie_titles = movies_meta["movie_titles"]
    ratings_per_user = ratings_meta["ratings_per_user"]
    min_timestamp_per_user = ratings_meta["min_timestamp_per_user"]
    max_timestamp_per_user = ratings_meta["max_timestamp_per_user"]
    ratings_per_movie = ratings_meta["ratings_per_movie"]
    rating_sum_per_movie = ratings_meta["rating_sum_per_movie"]
    tags_per_user = tags_meta["tags_per_user"]
    tags_per_movie = tags_meta["tags_per_movie"]
    sequence_thresholds = [20, 50, 100, 200, 500, 1000]

    user_count = len(ratings_per_user)
    movie_count = len(movie_titles)
    ratings_total = ratings_meta["ratings_total"]
    tags_total = tags_meta["tags_total"]
    matrix_density = ratings_total / (user_count * movie_count)

    user_sequence_lengths, user_sequence_spans = build_user_sequence_arrays(
        ratings_per_user,
        min_timestamp_per_user,
        max_timestamp_per_user,
    )
    movie_rating_counts = np.fromiter(ratings_per_movie.values(), dtype=np.int64)
    tag_user_counts = np.fromiter(tags_per_user.values(), dtype=np.int64)
    user_activity = quantile_summary(user_sequence_lengths)
    movie_popularity = quantile_summary(movie_rating_counts)
    tag_user_activity = quantile_summary(tag_user_counts)
    user_sequence_span_summary = quantile_summary(user_sequence_spans)
    user_sequence_span_bucket_stats = span_bucket_stats(user_sequence_spans)
    span_threshold_stats = span_stats_by_sequence_threshold(
        user_sequence_lengths,
        user_sequence_spans,
        sequence_thresholds,
    )

    movie_tail_rows = [
        [f"<= {threshold} ratings", format_int(matched), format_float(share, 2) + "%"]
        for threshold, matched, share in at_most_stats(movie_rating_counts, [1, 2, 5, 10, 20, 50, 100])
    ]
    movie_head_rows = [
        [f"Top {int(fraction * 100)}% movies", f"{format_int(top_n)} / {format_int(total_n)}", format_float(share, 2) + "%"]
        for fraction, top_n, total_n, share in concentration_stats(movie_rating_counts)
    ]
    user_head_rows = [
        [f"Top {int(fraction * 100)}% users", f"{format_int(top_n)} / {format_int(total_n)}", format_float(share, 2) + "%"]
        for fraction, top_n, total_n, share in concentration_stats(user_sequence_lengths)
    ]

    ratings_distribution_rows = [
        [str(rating), format_int(count), percent(count, ratings_total)]
        for rating, count in sorted(ratings_meta["rating_value_counts"].items())
    ]
    user_activity_rows = [
        [label, format_count_stat(value)]
        for label, value in [
            ("min", user_activity["min"]),
            ("p25", user_activity["p25"]),
            ("median", user_activity["median"]),
            ("p75", user_activity["p75"]),
            ("p90", user_activity["p90"]),
            ("p99", user_activity["p99"]),
            ("max", user_activity["max"]),
            ("mean", user_activity["mean"]),
        ]
    ]
    movie_popularity_rows = [
        [label, format_count_stat(value)]
        for label, value in [
            ("min", movie_popularity["min"]),
            ("p25", movie_popularity["p25"]),
            ("median", movie_popularity["median"]),
            ("p75", movie_popularity["p75"]),
            ("p90", movie_popularity["p90"]),
            ("p99", movie_popularity["p99"]),
            ("max", movie_popularity["max"]),
            ("mean", movie_popularity["mean"]),
        ]
    ]
    tag_activity_rows = [
        [label, format_count_stat(value)]
        for label, value in [
            ("min", tag_user_activity["min"]),
            ("p25", tag_user_activity["p25"]),
            ("median", tag_user_activity["median"]),
            ("p75", tag_user_activity["p75"]),
            ("p90", tag_user_activity["p90"]),
            ("p99", tag_user_activity["p99"]),
            ("max", tag_user_activity["max"]),
            ("mean", tag_user_activity["mean"]),
        ]
    ]
    user_span_rows = [
        [label, format_float(value, 1)]
        for label, value in [
            ("min", user_sequence_span_summary["min"]),
            ("p25", user_sequence_span_summary["p25"]),
            ("median", user_sequence_span_summary["median"]),
            ("p75", user_sequence_span_summary["p75"]),
            ("p90", user_sequence_span_summary["p90"]),
            ("p99", user_sequence_span_summary["p99"]),
            ("max", user_sequence_span_summary["max"]),
            ("mean", user_sequence_span_summary["mean"]),
        ]
    ]
    span_bucket_rows = [
        [label, format_int(count), format_float(share, 2) + "%"]
        for label, (_, _, count, share) in zip(
            ["<= 0.1 days", "0.1-1 days", "1-7 days", "7-30 days", "30-365 days", "> 365 days"],
            user_sequence_span_bucket_stats,
        )
    ]
    span_threshold_rows = [
        [f">= {format_int(threshold)} ratings", format_int(filtered_users), format_float(median_span, 1), format_float(mean_span, 1)]
        for threshold, filtered_users, median_span, mean_span in span_threshold_stats
    ]
    genre_movie_rows = [
        [genre, format_int(count), percent(count, movie_count)]
        for genre, count in movies_meta["genre_movie_counts"].most_common(15)
    ]
    genre_rating_rows = [[genre, format_int(count), format_float(avg_rating, 3)] for genre, count, avg_rating in genre_rating_summary[:15]]
    top_tags_rows = [
        [tag, format_int(count), percent(count, tags_total)]
        for tag, count in tags_meta["normalized_tag_counts"].most_common(15)
    ]
    temporal_rows = [
        [str(year), format_int(ratings_meta["ratings_by_year"].get(year, 0)), format_int(tags_meta["tags_by_year"].get(year, 0))]
        for year in sorted(set(ratings_meta["ratings_by_year"]) | set(tags_meta["tags_by_year"]))
    ]

    lines = [
        "# MovieLens ml-32m EDA Report",
        "",
        f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Dataset Overview",
        "",
        markdown_table(
            ["Metric", "Value"],
            [
                ["Movies", format_int(movie_count)],
                ["Ratings", format_int(ratings_total)],
                ["Tags", format_int(tags_total)],
                ["Users with ratings", format_int(user_count)],
                ["Users with tags", format_int(len(tags_per_user))],
                ["Movies with tags", format_int(len(tags_per_movie))],
                ["User-movie matrix density", format_float(matrix_density * 100, 4) + "%"],
                ["Ratings date range", f"{utc_date_str(ratings_meta['min_timestamp'])} to {utc_date_str(ratings_meta['max_timestamp'])}"],
                ["Tags date range", f"{utc_date_str(tags_meta['min_timestamp'])} to {utc_date_str(tags_meta['max_timestamp'])}"],
            ],
        ),
        "",
        "## Data Quality Checks",
        "",
        markdown_table(
            ["Check", "Value"],
            [
                ["Duplicate movieId rows in movies.csv", format_int(movies_meta["duplicate_movie_ids"])],
                ["Duplicate movieId rows in links.csv", format_int(links_meta["duplicate_movie_ids"])],
                ["Movie titles without release year pattern", format_int(movies_meta["titles_missing_year"])],
                ["Movies with '(no genres listed)'", format_int(movies_meta["no_genre_movies"])],
                ["Links missing imdbId", format_int(links_meta["missing_imdb"])],
                ["Links missing tmdbId", format_int(links_meta["missing_tmdb"])],
                ["Repeated consecutive userId-movieId pairs in ratings.csv", format_int(ratings_meta["repeated_user_movie_pairs"])],
                ["Blank normalized tags", format_int(tags_meta["blank_tags"])],
            ],
        ),
        "",
        "## Ratings Distribution",
        "",
        markdown_table(["Rating", "Count", "Share"], ratings_distribution_rows),
        "",
        "![Rating Distribution](ratings_distribution.png)",
        "",
        "## User Activity",
        "",
        markdown_table(["Statistic", "Ratings per user"], user_activity_rows),
        "",
        markdown_table(["Threshold", "Users", "Share"], threshold_rows_from_array(user_sequence_lengths, sequence_thresholds, "ratings")),
        "",
        "![Sequence Threshold Coverage](sequence_threshold_coverage.png)",
        "",
        markdown_table(["Statistic", "User activity span (days)"], user_span_rows),
        "",
        markdown_table(["Span bucket", "Users", "Share"], span_bucket_rows),
        "",
        "![User Activity Span Buckets](user_activity_span_buckets.png)",
        "",
        markdown_table(["User filter", "Users", "Median span (days)", "Mean span (days)"], span_threshold_rows),
        "",
        markdown_table(
            ["userId", "Sequence length", "Span (days)", "First rating", "Last rating"],
            top_users_by_sequence_length(ratings_per_user, min_timestamp_per_user, max_timestamp_per_user),
        ),
        "",
        markdown_table(["Statistic", "Tags per tagging user"], tag_activity_rows),
        "",
        "## Movie Popularity",
        "",
        markdown_table(["Statistic", "Ratings per movie"], movie_popularity_rows),
        "",
        markdown_table(
            ["movieId", "Title", "Ratings", "Average rating"],
            top_movies_by_count(movie_titles, ratings_per_movie, rating_sum_per_movie),
        ),
        "",
        "### Highest Rated Movies with at Least 1,000 Ratings",
        "",
        markdown_table(
            ["movieId", "Title", "Ratings", "Average rating"],
            top_movies_by_average(movie_titles, ratings_per_movie, rating_sum_per_movie, min_ratings=1_000),
        ),
        "",
        "### Most Tagged Movies",
        "",
        markdown_table(["movieId", "Title", "Tags"], top_movies_by_tags(movie_titles, tags_per_movie)),
        "",
        "![Ratings Per User Long Tail](ratings_per_user_long_tail.png)",
        "",
        "![Ratings Per Movie Long Tail](ratings_per_movie_long_tail.png)",
        "",
        "## Long-Tail Concentration",
        "",
        markdown_table(["Movie bucket", "Movies", "Share of movies"], movie_tail_rows),
        "",
        markdown_table(["Head bucket", "Coverage", "Share of all ratings"], movie_head_rows),
        "",
        markdown_table(["Heavy-user bucket", "Coverage", "Share of all ratings"], user_head_rows),
        "",
        "## Genres",
        "",
        markdown_table(["Genre", "Movies", "Share of movies"], genre_movie_rows),
        "",
        markdown_table(["Genre", "Ratings", "Average rating"], genre_rating_rows),
        "",
        "## Tags",
        "",
        markdown_table(["Tag", "Count", "Share"], top_tags_rows),
        "",
        "## Temporal Activity",
        "",
        markdown_table(["Year", "Ratings", "Tags"], temporal_rows),
        "",
        "![Activity By Year](activity_by_year.png)",
        "",
    ]

    return write_markdown(report_path, lines)


def write_dataset_description_report_ko(
    movies_meta: dict,
    links_meta: dict,
    ratings_meta: dict,
    tags_meta: dict,
) -> str:
    report_path = os.path.join(OUTPUT_DIR, "dataset_description_report_ko.md")

    movie_titles = movies_meta["movie_titles"]
    ratings_per_user = ratings_meta["ratings_per_user"]
    ratings_per_movie = ratings_meta["ratings_per_movie"]
    rating_sum_per_movie = ratings_meta["rating_sum_per_movie"]
    tags_per_user = tags_meta["tags_per_user"]
    tags_per_movie = tags_meta["tags_per_movie"]
    min_timestamp_per_user = ratings_meta["min_timestamp_per_user"]
    max_timestamp_per_user = ratings_meta["max_timestamp_per_user"]

    movie_count = len(movie_titles)
    user_count = len(ratings_per_user)
    ratings_total = ratings_meta["ratings_total"]
    tags_total = tags_meta["tags_total"]
    tagging_user_count = len(tags_per_user)
    tagged_movie_count = len(tags_per_movie)
    sequence_thresholds = [20, 50, 100, 200, 500, 1000]

    matrix_density = ratings_total / (user_count * movie_count)
    average_rating = sum(rating * count for rating, count in ratings_meta["rating_value_counts"].items()) / ratings_total
    high_rating_share = sum(count for rating, count in ratings_meta["rating_value_counts"].items() if rating >= 4.0) / ratings_total
    user_sequence_lengths, user_sequence_spans = build_user_sequence_arrays(
        ratings_per_user,
        min_timestamp_per_user,
        max_timestamp_per_user,
    )
    movie_rating_counts = np.fromiter(ratings_per_movie.values(), dtype=np.int64)
    user_activity = quantile_summary(user_sequence_lengths)
    movie_popularity = quantile_summary(movie_rating_counts)
    user_sequence_span_summary = quantile_summary(user_sequence_spans)
    movie_head_rows = [
        [f"상위 {int(fraction * 100)}% 영화", f"{format_int(top_n)} / {format_int(total_n)}", format_float(share, 2) + "%"]
        for fraction, top_n, total_n, share in concentration_stats(movie_rating_counts)
    ]
    user_head_rows = [
        [f"상위 {int(fraction * 100)}% 사용자", f"{format_int(top_n)} / {format_int(total_n)}", format_float(share, 2) + "%"]
        for fraction, top_n, total_n, share in concentration_stats(user_sequence_lengths)
    ]

    lines = [
        "# MovieLens ml-32m 데이터셋 설명 보고서",
        "",
        f"작성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 데이터셋 개요",
        "",
        markdown_table(
            ["항목", "값"],
            [
                ["영화 수", format_int(movie_count)],
                ["평점 수", format_int(ratings_total)],
                ["태그 수", format_int(tags_total)],
                ["평점 사용자 수", format_int(user_count)],
                ["태그 사용자 수", format_int(tagging_user_count)],
                ["태그가 붙은 영화 수", format_int(tagged_movie_count)],
                ["행렬 밀도", format_float(matrix_density * 100, 4) + "%"],
                ["평균 평점", format_float(average_rating, 4)],
                ["4.0점 이상 비중", format_float(high_rating_share * 100, 2) + "%"],
                ["평점 기간", f"{utc_date_str(ratings_meta['min_timestamp'])} ~ {utc_date_str(ratings_meta['max_timestamp'])}"],
                ["태그 기간", f"{utc_date_str(tags_meta['min_timestamp'])} ~ {utc_date_str(tags_meta['max_timestamp'])}"],
            ],
        ),
        "",
        "이 데이터는 MovieLens 서비스의 사용자-영화 상호작용 로그다. 핵심 강점은 대규모 평점 수, 긴 시계열 범위, 그리고 영화 메타데이터와 자유 태그가 함께 있다는 점이다.",
        "",
        "## 2. 파일 구성",
        "",
        markdown_table(
            ["파일", "행 수", "핵심 컬럼", "역할"],
            [
                ["`movies.csv`", format_int(movie_count), "`movieId`, `title`, `genres`", "영화 메타데이터"],
                ["`ratings.csv`", format_int(ratings_total), "`userId`, `movieId`, `rating`, `timestamp`", "평점 이벤트 로그"],
                ["`tags.csv`", format_int(tags_total), "`userId`, `movieId`, `tag`, `timestamp`", "자유 태그 로그"],
                ["`links.csv`", format_int(links_meta["total_links"]), "`movieId`, `imdbId`, `tmdbId`", "외부 식별자 연결"],
            ],
        ),
        "",
        "## 3. 사용자 시퀀스 관점",
        "",
        markdown_table(
            ["지표", "값"],
            [
                ["사용자당 평점 중앙값", format_count_stat(user_activity["median"])],
                ["사용자당 평점 평균", format_count_stat(user_activity["mean"])],
                ["사용자당 평점 99퍼센타일", format_count_stat(user_activity["p99"])],
                ["최대 평점 수", format_count_stat(user_activity["max"])],
                ["활동 기간 중앙값", format_float(user_sequence_span_summary["median"], 1) + "일"],
                ["활동 기간 평균", format_float(user_sequence_span_summary["mean"], 1) + "일"],
            ],
        ),
        "",
        markdown_table(["Threshold", "Users", "Share"], threshold_rows_from_array(user_sequence_lengths, sequence_thresholds, "평점")),
        "",
        "모든 사용자가 최소 20개 평점을 갖고 시작하지만, 분포는 매우 비대칭이다. 짧은 사용자도 많고 소수의 헤비 유저가 극단적으로 긴 시퀀스를 만든다.",
        "",
        "## 4. 아이템 인기도와 롱테일",
        "",
        markdown_table(
            ["지표", "값"],
            [
                ["영화당 평점 중앙값", format_count_stat(movie_popularity["median"])],
                ["영화당 평점 평균", format_count_stat(movie_popularity["mean"])],
                ["영화당 평점 99퍼센타일", format_count_stat(movie_popularity["p99"])],
                ["최다 평점 영화", format_count_stat(movie_popularity["max"])],
            ],
        ),
        "",
        markdown_table(["인기 집중 구간", "커버리지", "전체 평점 점유율"], movie_head_rows),
        "",
        markdown_table(["헤비 유저 구간", "커버리지", "전체 평점 점유율"], user_head_rows),
        "",
        "이 표가 의미하는 바는 간단하다. 소수의 인기 영화와 소수의 헤비 유저가 전체 상호작용의 상당 부분을 차지한다. 따라서 추천 실험에서는 전체 성능만 보지 말고 head/tail 아이템을 분리해 보는 것이 안전하다.",
        "",
        "## 5. 해석 시 주의점",
        "",
        "- `ratings.csv`는 사용자 내부에서 시간순이 아니라 `movieId` 순 정렬이다. 시퀀스 모델용으로 쓰려면 `timestamp` 기준 재정렬이 필요하다.",
        "- `timestamp`는 시청 시각이 아니라 평점을 남긴 시각이다. 취향 변화 해석에 과도하게 쓰면 안 된다.",
        "- 자유 태그는 표기 흔들림과 동의어 중복이 있다. 정규화 전처리가 필요하다.",
        "- 일부 항목은 영화 외 시리즈성 콘텐츠를 포함할 수 있다.",
        "",
        "## 6. 추천 시스템용 해석",
        "",
        "이 소스는 두 데이터 중에서 시퀀스 추천과 adaptive multi-interest 실험의 기본 상호작용 로그로 쓰기에 가장 적합하다. 이유는 명확하다. 사용자 수가 크고, 평점 이벤트가 많고, 무엇보다 시간 정보가 있다.",
        "",
    ]

    return write_markdown(report_path, lines)


def run() -> dict:
    movies_meta = load_movies()
    links_meta = load_links()
    ratings_meta = load_ratings()
    tags_meta = load_tags()

    genre_rating_summary = build_genre_rating_summary(
        movies_meta["movie_genres"],
        ratings_meta["ratings_per_movie"],
        ratings_meta["rating_sum_per_movie"],
    )

    user_sequence_lengths, user_sequence_spans = build_user_sequence_arrays(
        ratings_meta["ratings_per_user"],
        ratings_meta["min_timestamp_per_user"],
        ratings_meta["max_timestamp_per_user"],
    )
    movie_rating_counts = np.fromiter(ratings_meta["ratings_per_movie"].values(), dtype=np.int64)

    plot_rating_distribution(OUTPUT_DIR, ratings_meta["rating_value_counts"])
    plot_dual_line_chart(
        OUTPUT_DIR,
        "activity_by_year.png",
        ratings_meta["ratings_by_year"],
        tags_meta["tags_by_year"],
        first_label="Ratings",
        second_label="Tags",
        title="MovieLens Activity by Year",
    )
    plot_long_tail_histogram(
        OUTPUT_DIR,
        user_sequence_lengths,
        title="Ratings per User Long Tail",
        x_label="ratings per user",
        filename="ratings_per_user_long_tail.png",
    )
    plot_long_tail_histogram(
        OUTPUT_DIR,
        movie_rating_counts,
        title="Ratings per Movie Long Tail",
        x_label="ratings per movie",
        filename="ratings_per_movie_long_tail.png",
    )
    plot_threshold_coverage(
        OUTPUT_DIR,
        "sequence_threshold_coverage.png",
        user_sequence_lengths,
        [20, 50, 100, 200, 500, 1000],
        title="Users Remaining by Sequence Length Threshold",
        x_label="Minimum ratings per user",
        entity_label="Users",
    )
    plot_span_buckets(
        OUTPUT_DIR,
        "user_activity_span_buckets.png",
        user_sequence_spans,
        title="Distribution of User Activity Span",
    )

    eda_report_path = write_eda_report(movies_meta, links_meta, ratings_meta, tags_meta, genre_rating_summary)
    dataset_report_path = write_dataset_description_report_ko(movies_meta, links_meta, ratings_meta, tags_meta)

    movie_head = concentration_stats(movie_rating_counts)
    user_head = concentration_stats(user_sequence_lengths)

    return {
        "source_id": "ml_32m",
        "source_name": "MovieLens ml-32m",
        "output_dir": OUTPUT_DIR,
        "eda_report_path": eda_report_path,
        "dataset_report_path": dataset_report_path,
        "movie_ids": set(movies_meta["movie_titles"].keys()),
        "movie_count": len(movies_meta["movie_titles"]),
        "rated_movie_count": len(ratings_meta["ratings_per_movie"]),
        "ratings_total": ratings_meta["ratings_total"],
        "user_count": len(ratings_meta["ratings_per_user"]),
        "tag_total": tags_meta["tags_total"],
        "tagging_user_count": len(tags_meta["tags_per_user"]),
        "tagged_movie_count": len(tags_meta["tags_per_movie"]),
        "review_total": 0,
        "survey_answer_total": 0,
        "score_rows_total": 0,
        "has_timestamps": True,
        "item_key_name": "movieId",
        "user_key_name": "userId",
        "head_movie_share_top1": movie_head[0][3] if movie_head else 0.0,
        "head_user_share_top10": user_head[-1][3] if user_head else 0.0,
    }
