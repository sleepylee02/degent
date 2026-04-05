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
    concentration_stats,
    dataset_output_dir,
    format_count_stat,
    format_float,
    format_int,
    load_jsonl,
    markdown_table,
    parse_movie_year,
    percent,
    plot_bar_chart,
    plot_long_tail_histogram,
    plot_rating_distribution,
    plot_threshold_coverage,
    quantile_summary,
    resolve_existing_dir,
    threshold_rows_from_array,
    write_markdown,
)

DATASET_DIR = resolve_existing_dir(
    "Genome 2021 dataset directory",
    [
        os.path.join(REPO_ROOT, "data", "genome_2021", "raw"),
        os.path.join(REPO_ROOT, "data", "genome_2021", "movie_dataset_public_final"),
        os.path.join(REPO_ROOT, "data", "genome_2021"),
        os.path.join(BASE_DIR, "genome_2021", "movie_dataset_public_final"),
        os.path.join(BASE_DIR, "genome_2021"),
    ],
)
RAW_DIR = os.path.join(DATASET_DIR, "raw")
SCORES_DIR = os.path.join(DATASET_DIR, "scores")
OUTPUT_DIR = dataset_output_dir("genome_2021")

METADATA_JSON = os.path.join(RAW_DIR, "metadata.json")
RATINGS_JSON = os.path.join(RAW_DIR, "ratings.json")
REVIEWS_JSON = os.path.join(RAW_DIR, "reviews.json")
TAGS_JSON = os.path.join(RAW_DIR, "tags.json")
TAG_COUNT_JSON = os.path.join(RAW_DIR, "tag_count.json")
SURVEY_JSON = os.path.join(RAW_DIR, "survey_answers.json")
GLMER_CSV = os.path.join(SCORES_DIR, "glmer.csv")
TAGDL_CSV = os.path.join(SCORES_DIR, "tagdl.csv")


def load_metadata() -> dict:
    print("[genome2021:metadata] loading metadata.json...", flush=True)
    movie_titles: dict[int, str] = {}
    missing_title_year = 0
    missing_directors = 0
    missing_starring = 0
    missing_date_added = 0
    missing_avg_rating = 0
    imdb_missing = 0

    for idx, row in enumerate(load_jsonl(METADATA_JSON), start=1):
        item_id = int(row["item_id"])
        title = row.get("title") or ""
        movie_titles[item_id] = title

        if parse_movie_year(title) is None:
            missing_title_year += 1
        if not (row.get("directedBy") or "").strip():
            missing_directors += 1
        if not (row.get("starring") or "").strip():
            missing_starring += 1
        if row.get("dateAdded") in (None, ""):
            missing_date_added += 1
        if row.get("avgRating") is None:
            missing_avg_rating += 1
        if not (row.get("imdbId") or "").strip():
            imdb_missing += 1

        if idx % 25_000 == 0:
            print(f"[genome2021:metadata] processed {format_int(idx)} rows...", flush=True)

    return {
        "movie_titles": movie_titles,
        "missing_title_year": missing_title_year,
        "missing_directors": missing_directors,
        "missing_starring": missing_starring,
        "missing_date_added": missing_date_added,
        "missing_avg_rating": missing_avg_rating,
        "missing_imdb_id": imdb_missing,
    }


def load_ratings() -> dict:
    print("[genome2021:ratings] streaming ratings.json...", flush=True)
    ratings_total = 0
    rating_value_counts: Counter[float] = Counter()
    ratings_per_user: defaultdict[int, int] = defaultdict(int)
    ratings_per_movie: defaultdict[int, int] = defaultdict(int)
    rating_sum_per_movie: defaultdict[int, float] = defaultdict(float)

    for row in load_jsonl(RATINGS_JSON):
        ratings_total += 1
        user_id = int(row["user_id"])
        item_id = int(row["item_id"])
        rating = float(row["rating"])

        rating_value_counts[rating] += 1
        ratings_per_user[user_id] += 1
        ratings_per_movie[item_id] += 1
        rating_sum_per_movie[item_id] += rating

        if ratings_total % 5_000_000 == 0:
            print(f"[genome2021:ratings] processed {format_int(ratings_total)} rows...", flush=True)

    return {
        "ratings_total": ratings_total,
        "rating_value_counts": rating_value_counts,
        "ratings_per_user": ratings_per_user,
        "ratings_per_movie": ratings_per_movie,
        "rating_sum_per_movie": rating_sum_per_movie,
    }


def load_reviews() -> dict:
    print("[genome2021:reviews] streaming reviews.json...", flush=True)
    review_total = 0
    reviews_per_movie: defaultdict[int, int] = defaultdict(int)

    for row in load_jsonl(REVIEWS_JSON):
        review_total += 1
        item_id = int(row["item_id"])
        reviews_per_movie[item_id] += 1

        if review_total % 500_000 == 0:
            print(f"[genome2021:reviews] processed {format_int(review_total)} rows...", flush=True)

    return {
        "review_total": review_total,
        "reviews_per_movie": reviews_per_movie,
    }


def load_tag_catalog() -> dict:
    print("[genome2021:tags] loading tags.json...", flush=True)
    tag_id_to_name: dict[int, str] = {}
    for row in load_jsonl(TAGS_JSON):
        tag_id_to_name[int(row["id"])] = row["tag"]
    return {"tag_id_to_name": tag_id_to_name}


def load_tag_counts(tag_id_to_name: dict[int, str]) -> dict:
    print("[genome2021:tag_count] streaming tag_count.json...", flush=True)
    movie_tag_pair_total = 0
    total_tag_applications = 0
    unique_tags_per_movie: defaultdict[int, int] = defaultdict(int)
    tag_applications_per_movie: defaultdict[int, int] = defaultdict(int)
    tag_applications_per_tag: defaultdict[int, int] = defaultdict(int)

    for row in load_jsonl(TAG_COUNT_JSON):
        movie_tag_pair_total += 1
        item_id = int(row["item_id"])
        tag_id = int(row["tag_id"])
        num = int(row["num"])

        unique_tags_per_movie[item_id] += 1
        tag_applications_per_movie[item_id] += num
        tag_applications_per_tag[tag_id] += num
        total_tag_applications += num

        if movie_tag_pair_total % 100_000 == 0:
            print(f"[genome2021:tag_count] processed {format_int(movie_tag_pair_total)} rows...", flush=True)

    top_tags = sorted(tag_applications_per_tag.items(), key=lambda item: item[1], reverse=True)[:15]

    return {
        "movie_tag_pair_total": movie_tag_pair_total,
        "total_tag_applications": total_tag_applications,
        "unique_tags_per_movie": unique_tags_per_movie,
        "tag_applications_per_movie": tag_applications_per_movie,
        "tag_applications_per_tag": tag_applications_per_tag,
        "top_tags": [[tag_id_to_name.get(tag_id, f"tag_id={tag_id}"), format_int(total)] for tag_id, total in top_tags],
    }


def load_survey_answers() -> dict:
    print("[genome2021:survey] streaming survey_answers.json...", flush=True)
    answer_total = 0
    score_counts: Counter[int] = Counter()
    users: set[int] = set()
    movies: set[int] = set()
    tags: set[int] = set()

    for row in load_jsonl(SURVEY_JSON):
        answer_total += 1
        score = int(row["score"])
        score_counts[score] += 1
        users.add(int(row["user_id"]))
        movies.add(int(row["item_id"]))
        tags.add(int(row["tag_id"]))

    not_sure_total = score_counts.get(-1, 0)
    return {
        "answer_total": answer_total,
        "score_counts": score_counts,
        "user_count": len(users),
        "movie_count": len(movies),
        "tag_count": len(tags),
        "not_sure_total": not_sure_total,
        "valid_answer_total": answer_total - not_sure_total,
    }


def load_score_file(path: str, label: str) -> dict:
    print(f"[genome2021:{label}] scanning {os.path.basename(path)}...", flush=True)
    row_total = 0
    score_sum = 0.0
    min_score: float | None = None
    max_score: float | None = None
    movie_ids: set[int] = set()
    tags: set[str] = set()
    below_zero = 0
    above_one = 0

    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            row_total += 1
            tag = row[0]
            item_id = int(row[1])
            score = float(row[2])

            movie_ids.add(item_id)
            tags.add(tag)
            score_sum += score

            if min_score is None or score < min_score:
                min_score = score
            if max_score is None or score > max_score:
                max_score = score
            if score < 0:
                below_zero += 1
            if score > 1:
                above_one += 1

            if row_total % 2_000_000 == 0:
                print(f"[genome2021:{label}] processed {format_int(row_total)} rows...", flush=True)

    return {
        "row_total": row_total,
        "movie_count": len(movie_ids),
        "tag_count": len(tags),
        "mean_score": 0.0 if row_total == 0 else score_sum / row_total,
        "min_score": 0.0 if min_score is None else min_score,
        "max_score": 0.0 if max_score is None else max_score,
        "below_zero": below_zero,
        "above_one": above_one,
    }


def top_items_by_count(
    movie_titles: dict[int, str],
    counts: dict[int, int],
    limit: int = 10,
) -> list[list[str]]:
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [[str(item_id), movie_titles.get(item_id, f"item_id={item_id}"), format_int(count)] for item_id, count in ranked]


def top_items_by_average(
    movie_titles: dict[int, str],
    ratings_per_movie: dict[int, int],
    rating_sum_per_movie: dict[int, float],
    min_ratings: int,
    limit: int = 10,
) -> list[list[str]]:
    candidates = []
    for item_id, count in ratings_per_movie.items():
        if count < min_ratings:
            continue
        candidates.append((item_id, count, rating_sum_per_movie[item_id] / count))

    ranked = sorted(candidates, key=lambda item: (-item[2], -item[1], item[0]))[:limit]
    return [
        [str(item_id), movie_titles.get(item_id, f"item_id={item_id}"), format_int(count), format_float(avg, 3)]
        for item_id, count, avg in ranked
    ]


def write_eda_report(
    metadata: dict,
    ratings: dict,
    reviews: dict,
    tag_catalog: dict,
    tag_counts: dict,
    survey: dict,
    glmer_scores: dict,
    tagdl_scores: dict,
) -> str:
    report_path = os.path.join(OUTPUT_DIR, "eda_report.md")

    movie_titles = metadata["movie_titles"]
    rating_user_counts = np.fromiter(ratings["ratings_per_user"].values(), dtype=np.int64)
    rating_movie_counts = np.fromiter(ratings["ratings_per_movie"].values(), dtype=np.int64)
    review_movie_counts = np.fromiter(reviews["reviews_per_movie"].values(), dtype=np.int64)
    tag_app_movie_counts = np.fromiter(tag_counts["tag_applications_per_movie"].values(), dtype=np.int64)
    unique_tags_movie_counts = np.fromiter(tag_counts["unique_tags_per_movie"].values(), dtype=np.int64)

    rating_user_summary = quantile_summary(rating_user_counts)
    rating_movie_summary = quantile_summary(rating_movie_counts)
    review_movie_summary = quantile_summary(review_movie_counts)
    tag_app_summary = quantile_summary(tag_app_movie_counts)
    unique_tag_summary = quantile_summary(unique_tags_movie_counts)

    lines = [
        "# Genome 2021 Movie Dataset EDA Report",
        "",
        f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Dataset Overview",
        "",
        markdown_table(
            ["Metric", "Value"],
            [
                ["Metadata movies", format_int(len(movie_titles))],
                ["Ratings", format_int(ratings["ratings_total"])],
                ["Users with ratings", format_int(len(ratings["ratings_per_user"]))],
                ["Movies with ratings", format_int(len(ratings["ratings_per_movie"]))],
                ["Reviews", format_int(reviews["review_total"])],
                ["Movies with reviews", format_int(len(reviews["reviews_per_movie"]))],
                ["Tag vocabulary", format_int(len(tag_catalog["tag_id_to_name"]))],
                ["Movie-tag count pairs", format_int(tag_counts["movie_tag_pair_total"])],
                ["Total tag applications", format_int(tag_counts["total_tag_applications"])],
                ["Movies with tag counts", format_int(len(tag_counts["tag_applications_per_movie"]))],
                ["Survey answers", format_int(survey["answer_total"])],
                ["Survey users", format_int(survey["user_count"])],
                ["Survey movies", format_int(survey["movie_count"])],
                ["Survey tags", format_int(survey["tag_count"])],
                ["GLMER score rows", format_int(glmer_scores["row_total"])],
                ["TagDL score rows", format_int(tagdl_scores["row_total"])],
            ],
        ),
        "",
        "## Metadata Quality",
        "",
        markdown_table(
            ["Check", "Value"],
            [
                ["Titles without year pattern", format_int(metadata["missing_title_year"])],
                ["Missing directors", format_int(metadata["missing_directors"])],
                ["Missing starring cast", format_int(metadata["missing_starring"])],
                ["Missing dateAdded", format_int(metadata["missing_date_added"])],
                ["Missing avgRating", format_int(metadata["missing_avg_rating"])],
                ["Missing imdbId", format_int(metadata["missing_imdb_id"])],
                ["Metadata movies without ratings", format_int(len(movie_titles) - len(ratings["ratings_per_movie"]))],
                ["Metadata movies without reviews", format_int(len(movie_titles) - len(reviews["reviews_per_movie"]))],
                ["Metadata movies without tag counts", format_int(len(movie_titles) - len(tag_counts["tag_applications_per_movie"]))],
            ],
        ),
        "",
        "## Ratings",
        "",
        markdown_table(
            ["Rating", "Count", "Share"],
            [
                [str(rating), format_int(count), percent(count, ratings["ratings_total"])]
                for rating, count in sorted(ratings["rating_value_counts"].items())
            ],
        ),
        "",
        "![Rating Distribution](ratings_distribution.png)",
        "",
        markdown_table(
            ["Statistic", "Ratings per user"],
            [
                [label, format_count_stat(value)]
                for label, value in [
                    ("min", rating_user_summary["min"]),
                    ("p25", rating_user_summary["p25"]),
                    ("median", rating_user_summary["median"]),
                    ("p75", rating_user_summary["p75"]),
                    ("p90", rating_user_summary["p90"]),
                    ("p99", rating_user_summary["p99"]),
                    ("max", rating_user_summary["max"]),
                    ("mean", rating_user_summary["mean"]),
                ]
            ],
        ),
        "",
        markdown_table(
            ["Threshold", "Users", "Share"],
            threshold_rows_from_array(rating_user_counts, [1, 5, 10, 20, 50, 100, 200], "ratings"),
        ),
        "",
        "![Ratings Threshold Coverage](ratings_threshold_coverage.png)",
        "",
        markdown_table(
            ["Statistic", "Ratings per movie"],
            [
                [label, format_count_stat(value)]
                for label, value in [
                    ("min", rating_movie_summary["min"]),
                    ("p25", rating_movie_summary["p25"]),
                    ("median", rating_movie_summary["median"]),
                    ("p75", rating_movie_summary["p75"]),
                    ("p90", rating_movie_summary["p90"]),
                    ("p99", rating_movie_summary["p99"]),
                    ("max", rating_movie_summary["max"]),
                    ("mean", rating_movie_summary["mean"]),
                ]
            ],
        ),
        "",
        markdown_table(
            ["movie bucket", "Movies", "Share of rated movies"],
            [
                [f"<= {threshold} ratings", format_int(matched), format_float(share, 2) + "%"]
                for threshold, matched, share in at_most_stats(rating_movie_counts, [1, 2, 5, 10, 20, 50, 100])
            ],
        ),
        "",
        markdown_table(
            ["Head bucket", "Coverage", "Share of all ratings"],
            [
                [f"Top {int(fraction * 100)}% movies", f"{format_int(top_n)} / {format_int(total_n)}", format_float(share, 2) + "%"]
                for fraction, top_n, total_n, share in concentration_stats(rating_movie_counts)
            ],
        ),
        "",
        markdown_table(
            ["Heavy-user bucket", "Coverage", "Share of all ratings"],
            [
                [f"Top {int(fraction * 100)}% users", f"{format_int(top_n)} / {format_int(total_n)}", format_float(share, 2) + "%"]
                for fraction, top_n, total_n, share in concentration_stats(rating_user_counts)
            ],
        ),
        "",
        markdown_table(
            ["item_id", "Title", "Ratings", "Average rating"],
            [
                row
                for row in [
                    [item_id, title, count, format_float(float(avg), 3)]
                    for item_id, title, count, avg in top_items_by_average(
                        movie_titles,
                        ratings["ratings_per_movie"],
                        ratings["rating_sum_per_movie"],
                        min_ratings=1_000,
                    )
                ]
            ],
        ),
        "",
        markdown_table(
            ["item_id", "Title", "Ratings"],
            top_items_by_count(movie_titles, ratings["ratings_per_movie"]),
        ),
        "",
        "![Ratings Per User Long Tail](ratings_per_user_long_tail.png)",
        "",
        "![Ratings Per Movie Long Tail](ratings_per_movie_long_tail.png)",
        "",
        "## Reviews And Tags",
        "",
        markdown_table(
            ["Statistic", "Reviews per movie"],
            [
                [label, format_count_stat(value)]
                for label, value in [
                    ("min", review_movie_summary["min"]),
                    ("p25", review_movie_summary["p25"]),
                    ("median", review_movie_summary["median"]),
                    ("p75", review_movie_summary["p75"]),
                    ("p90", review_movie_summary["p90"]),
                    ("p99", review_movie_summary["p99"]),
                    ("max", review_movie_summary["max"]),
                    ("mean", review_movie_summary["mean"]),
                ]
            ],
        ),
        "",
        markdown_table(["item_id", "Title", "Reviews"], top_items_by_count(movie_titles, reviews["reviews_per_movie"])),
        "",
        "![Reviews Per Movie Long Tail](reviews_per_movie_long_tail.png)",
        "",
        markdown_table(
            ["Statistic", "Tag applications per movie"],
            [
                [label, format_count_stat(value)]
                for label, value in [
                    ("min", tag_app_summary["min"]),
                    ("p25", tag_app_summary["p25"]),
                    ("median", tag_app_summary["median"]),
                    ("p75", tag_app_summary["p75"]),
                    ("p90", tag_app_summary["p90"]),
                    ("p99", tag_app_summary["p99"]),
                    ("max", tag_app_summary["max"]),
                    ("mean", tag_app_summary["mean"]),
                ]
            ],
        ),
        "",
        markdown_table(
            ["Statistic", "Unique tags per movie"],
            [
                [label, format_count_stat(value)]
                for label, value in [
                    ("min", unique_tag_summary["min"]),
                    ("p25", unique_tag_summary["p25"]),
                    ("median", unique_tag_summary["median"]),
                    ("p75", unique_tag_summary["p75"]),
                    ("p90", unique_tag_summary["p90"]),
                    ("p99", unique_tag_summary["p99"]),
                    ("max", unique_tag_summary["max"]),
                    ("mean", unique_tag_summary["mean"]),
                ]
            ],
        ),
        "",
        markdown_table(["item_id", "Title", "Tag applications"], top_items_by_count(movie_titles, tag_counts["tag_applications_per_movie"])),
        "",
        markdown_table(["Tag", "Applications"], tag_counts["top_tags"]),
        "",
        "![Tag Applications Per Movie Long Tail](tag_applications_per_movie_long_tail.png)",
        "",
        "## Survey And Tag-Relevance Scores",
        "",
        markdown_table(
            ["Score", "Count", "Share"],
            [[str(score), format_int(count), percent(count, survey["answer_total"])] for score, count in sorted(survey["score_counts"].items())],
        ),
        "",
        "![Survey Score Distribution](survey_score_distribution.png)",
        "",
        markdown_table(
            ["Model", "Rows", "Movies", "Tags", "Mean score", "Min", "Max", "< 0", "> 1"],
            [
                [
                    "GLMER",
                    format_int(glmer_scores["row_total"]),
                    format_int(glmer_scores["movie_count"]),
                    format_int(glmer_scores["tag_count"]),
                    format_float(glmer_scores["mean_score"], 4),
                    format_float(glmer_scores["min_score"], 4),
                    format_float(glmer_scores["max_score"], 4),
                    format_int(glmer_scores["below_zero"]),
                    format_int(glmer_scores["above_one"]),
                ],
                [
                    "TagDL",
                    format_int(tagdl_scores["row_total"]),
                    format_int(tagdl_scores["movie_count"]),
                    format_int(tagdl_scores["tag_count"]),
                    format_float(tagdl_scores["mean_score"], 4),
                    format_float(tagdl_scores["min_score"], 4),
                    format_float(tagdl_scores["max_score"], 4),
                    format_int(tagdl_scores["below_zero"]),
                    format_int(tagdl_scores["above_one"]),
                ],
            ],
        ),
        "",
        "This source is best understood as a MovieLens-derived semantic supervision dataset. It contains a large rating matrix, but the real differentiator is the combination of IMDb reviews, movie-tag counts, survey labels, and precomputed tag-relevance scores.",
        "",
    ]

    return write_markdown(report_path, lines)


def write_dataset_description_report_ko(
    metadata: dict,
    ratings: dict,
    reviews: dict,
    tag_catalog: dict,
    tag_counts: dict,
    survey: dict,
    glmer_scores: dict,
    tagdl_scores: dict,
) -> str:
    report_path = os.path.join(OUTPUT_DIR, "dataset_description_report_ko.md")

    movie_titles = metadata["movie_titles"]
    rating_user_counts = np.fromiter(ratings["ratings_per_user"].values(), dtype=np.int64)
    rating_movie_counts = np.fromiter(ratings["ratings_per_movie"].values(), dtype=np.int64)
    tag_app_movie_counts = np.fromiter(tag_counts["tag_applications_per_movie"].values(), dtype=np.int64)

    rating_user_summary = quantile_summary(rating_user_counts)
    rating_movie_summary = quantile_summary(rating_movie_counts)
    tag_app_summary = quantile_summary(tag_app_movie_counts)

    lines = [
        "# Genome 2021 영화 데이터셋 설명 보고서",
        "",
        f"작성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 이 데이터셋은 무엇인가",
        "",
        "이 소스는 일반적인 추천 로그만 담은 데이터가 아니라, Tag Genome 점수를 만들기 위해 모은 복합 영화 데이터셋이다. MovieLens 평점, MovieLens 태그 집계, IMDb 리뷰, 사용자 설문 응답, 그리고 태그-영화 relevance score까지 한 번에 포함한다.",
        "",
        "## 2. 핵심 구성",
        "",
        "MovieLens처럼 이 소스도 파일별 역할을 나눠서 보면 이해가 쉽다. 다만 차이는 분명하다. MovieLens가 `ratings/tags/movies/links` 중심의 추천 로그 구조라면, Genome 2021은 평점 로그 외에도 리뷰, 설문, relevance score를 함께 담은 복합 구조다.",
        "",
        "- `raw/metadata.json`",
        "",
        "  => 영화 제목, 감독, 배우, 평균 평점, IMDb id 같은 메타데이터를 담는다. MovieLens의 `movies.csv`와 `links.csv`를 합친 느낌에 가깝다.",
        "",
        "- `raw/ratings.json`",
        "",
        "  => 한 사용자가 한 영화에 남긴 평점 이벤트를 나타낸다. 관측 단위는 `user_id-item_id` 수준의 평점 1건이다. 다만 `timestamp`는 없다.",
        "",
        "- `raw/reviews.json`",
        "",
        "  => IMDb에서 수집한 영화 리뷰 텍스트다. 한 행이 한 개의 리뷰이며, 영화 semantic 정보를 풍부하게 만드는 핵심 소스다.",
        "",
        "- `raw/tags.json`",
        "",
        "  => tag vocabulary 사전이다. `tag_id`와 실제 태그 문자열을 연결한다.",
        "",
        "- `raw/tag_count.json`",
        "",
        "  => MovieLens 사용자들이 특정 태그를 특정 영화에 몇 번 붙였는지를 집계한 데이터다. 자유 텍스트 원문 로그가 아니라 `영화-태그 빈도` 테이블이다.",
        "",
        "- `raw/survey_answers.json`",
        "",
        "  => 사용자가 `이 태그가 이 영화에 얼마나 잘 맞는가`를 1~5점으로 평가한 설문 응답이다. `-1`은 확신 없음이다.",
        "",
        "- `scores/glmer.csv`",
        "",
        "  => 회귀 기반 알고리즘이 예측한 `영화-태그 relevance score`다. 점수는 대체로 0~1 범위다.",
        "",
        "- `scores/tagdl.csv`",
        "",
        "  => TagDL 신경망 모델이 예측한 `영화-태그 relevance score`다. 설계상 일부 값은 0 미만 또는 1 초과일 수 있다.",
        "",
        markdown_table(
            ["파일", "행 수", "주요 컬럼", "설명"],
            [
                ["`raw/metadata.json`", format_int(len(movie_titles)), "`item_id`, `title`, `directedBy`, `starring`, `avgRating`, `imdbId`", "영화 메타데이터"],
                ["`raw/ratings.json`", format_int(ratings["ratings_total"]), "`user_id`, `item_id`, `rating`", "평점 이벤트 로그"],
                ["`raw/reviews.json`", format_int(reviews["review_total"]), "`item_id`, `txt`", "IMDb 리뷰 텍스트"],
                ["`raw/tags.json`", format_int(len(tag_catalog["tag_id_to_name"])), "`id`, `tag`", "태그 사전"],
                ["`raw/tag_count.json`", format_int(tag_counts["movie_tag_pair_total"]), "`item_id`, `tag_id`, `num`", "영화-태그 집계 빈도"],
                ["`raw/survey_answers.json`", format_int(survey["answer_total"]), "`user_id`, `item_id`, `tag_id`, `score`", "태그 적합도 설문 응답"],
                ["`scores/glmer.csv`", format_int(glmer_scores["row_total"]), "`tag`, `item_id`, `score`", "회귀 기반 relevance score"],
                ["`scores/tagdl.csv`", format_int(tagdl_scores["row_total"]), "`tag`, `item_id`, `score`", "신경망 기반 relevance score"],
            ],
        ),
        "",
        markdown_table(
            ["구성요소", "규모", "역할"],
            [
                ["메타데이터 영화", format_int(len(movie_titles)), "제목, 감독, 배우, 평균 평점, IMDb id"],
                ["평점", format_int(ratings["ratings_total"]), "MovieLens 기반 사용자-영화 평점"],
                ["리뷰", format_int(reviews["review_total"]), "IMDb 텍스트 리뷰"],
                ["태그 어휘", format_int(len(tag_catalog["tag_id_to_name"])), "고정 tag vocabulary"],
                ["영화-태그 집계쌍", format_int(tag_counts["movie_tag_pair_total"]), "MovieLens에서 태그가 몇 번 붙었는지"],
                ["설문 응답", format_int(survey["answer_total"]), "영화-태그 pair에 대한 사용자 판단"],
                ["GLMER 점수", format_int(glmer_scores["row_total"]), "회귀 기반 tag relevance"],
                ["TagDL 점수", format_int(tagdl_scores["row_total"]), "신경망 기반 tag relevance"],
            ],
        ),
        "",
        "## 3. 평점 행렬 관점",
        "",
        markdown_table(
            ["지표", "값"],
            [
                ["평점 사용자 수", format_int(len(ratings["ratings_per_user"]))],
                ["평점 영화 수", format_int(len(ratings["ratings_per_movie"]))],
                ["사용자당 평점 중앙값", format_count_stat(rating_user_summary["median"])],
                ["영화당 평점 중앙값", format_count_stat(rating_movie_summary["median"])],
                ["영화당 평점 99퍼센타일", format_count_stat(rating_movie_summary["p99"])],
                ["최다 평점 영화", format_count_stat(rating_movie_summary["max"])],
            ],
        ),
        "",
        markdown_table(
            ["인기 집중 구간", "커버리지", "전체 평점 점유율"],
            [
                [f"상위 {int(fraction * 100)}% 영화", f"{format_int(top_n)} / {format_int(total_n)}", format_float(share, 2) + "%"]
                for fraction, top_n, total_n, share in concentration_stats(rating_movie_counts)
            ],
        ),
        "",
        "평점 행렬 자체도 분명히 롱테일이다. 다만 이 데이터에는 `timestamp`가 없기 때문에 시퀀스 추천용 원천 로그로 보기에는 한계가 있다. 여기서의 평점은 순서 정보보다 협업 신호와 영화별 평균 성향을 제공하는 역할에 가깝다.",
        "",
        "## 4. 이 데이터가 MovieLens와 다른 점",
        "",
        "- 리뷰 텍스트가 있다. 영화 의미를 설명하거나 텍스트 임베딩을 만들기에 좋다.",
        "- 자유 텍스트 태그 원문이 아니라 고정 tag vocabulary와 영화-태그 집계가 있다.",
        "- 설문 응답과 relevance score 파일이 있어 tag relevance prediction이나 설명 가능성 연구에 적합하다.",
        "- 평점은 많지만 시간 정보가 없어 adaptive sequence 실험의 주력 소스로 쓰기는 어렵다.",
        "",
        "## 5. 해석 시 주의점",
        "",
        "- 메타데이터 전체 영화 수와 평점/리뷰/태그 집계 커버리지는 다르다. 즉 메타데이터에 있다고 해서 모든 부가 정보가 다 있는 것은 아니다.",
        "- `survey_answers.json`의 `-1`은 확신 없음이다. 일반 점수와 섞어서 평균내면 안 된다.",
        "- `TagDL` 점수는 설계상 0 미만 또는 1 초과가 나올 수 있다.",
        "- 사용자 id는 이 데이터 내부에서는 일관되지만, 다른 MovieLens 버전과 동일 사용자라고 가정하는 것은 안전하지 않다.",
        "",
        "## 6. 언제 쓰면 좋은가",
        "",
        markdown_table(
            ["목적", "적합도", "이유"],
            [
                ["시퀀스 추천의 주력 로그", "낮음", "timestamp가 없다"],
                ["영화 semantic side feature", "매우 높음", "리뷰, tag count, relevance score가 풍부하다"],
                ["설명 가능한 추천", "높음", "영화-태그 연결 근거가 많다"],
                ["tag relevance prediction", "매우 높음", "설문 정답과 점수 파일이 있다"],
                ["롱테일 영화 의미 보강", "높음", "리뷰와 tag score를 함께 사용할 수 있다"],
            ],
        ),
        "",
        "이 소스는 추천 모델의 주 상호작용 로그라기보다, 영화 표현을 풍부하게 만드는 보조 지식 소스로 보는 편이 맞다.",
        "",
        "## 7. 참고 수치",
        "",
        markdown_table(
            ["지표", "값"],
            [
                ["태그 적용 수 중앙값", format_count_stat(tag_app_summary["median"])],
                ["태그 적용 수 평균", format_count_stat(tag_app_summary["mean"])],
                ["설문 응답 중 -1 비중", percent(survey["not_sure_total"], survey["answer_total"])],
                ["GLMER score 범위", f"{format_float(glmer_scores['min_score'], 4)} ~ {format_float(glmer_scores['max_score'], 4)}"],
                ["TagDL score 범위", f"{format_float(tagdl_scores['min_score'], 4)} ~ {format_float(tagdl_scores['max_score'], 4)}"],
            ],
        ),
        "",
    ]

    return write_markdown(report_path, lines)


def run() -> dict:
    metadata = load_metadata()
    ratings = load_ratings()
    reviews = load_reviews()
    tag_catalog = load_tag_catalog()
    tag_counts = load_tag_counts(tag_catalog["tag_id_to_name"])
    survey = load_survey_answers()
    glmer_scores = load_score_file(GLMER_CSV, "glmer")
    tagdl_scores = load_score_file(TAGDL_CSV, "tagdl")

    rating_user_counts = np.fromiter(ratings["ratings_per_user"].values(), dtype=np.int64)
    rating_movie_counts = np.fromiter(ratings["ratings_per_movie"].values(), dtype=np.int64)
    review_movie_counts = np.fromiter(reviews["reviews_per_movie"].values(), dtype=np.int64)
    tag_app_movie_counts = np.fromiter(tag_counts["tag_applications_per_movie"].values(), dtype=np.int64)

    plot_rating_distribution(OUTPUT_DIR, ratings["rating_value_counts"])
    plot_threshold_coverage(
        OUTPUT_DIR,
        "ratings_threshold_coverage.png",
        rating_user_counts,
        [1, 5, 10, 20, 50, 100, 200],
        title="Users Remaining by Ratings Threshold",
        x_label="Minimum ratings per user",
        entity_label="Users",
    )
    plot_long_tail_histogram(
        OUTPUT_DIR,
        rating_user_counts,
        title="Ratings per User Long Tail",
        x_label="ratings per user",
        filename="ratings_per_user_long_tail.png",
    )
    plot_long_tail_histogram(
        OUTPUT_DIR,
        rating_movie_counts,
        title="Ratings per Movie Long Tail",
        x_label="ratings per movie",
        filename="ratings_per_movie_long_tail.png",
    )
    plot_long_tail_histogram(
        OUTPUT_DIR,
        review_movie_counts,
        title="Reviews per Movie Long Tail",
        x_label="reviews per movie",
        filename="reviews_per_movie_long_tail.png",
    )
    plot_long_tail_histogram(
        OUTPUT_DIR,
        tag_app_movie_counts,
        title="Tag Applications per Movie Long Tail",
        x_label="tag applications per movie",
        filename="tag_applications_per_movie_long_tail.png",
    )
    survey_labels = [str(score) for score in sorted(survey["score_counts"])]
    survey_values = [survey["score_counts"][int(label)] for label in survey_labels]
    plot_bar_chart(
        OUTPUT_DIR,
        "survey_score_distribution.png",
        survey_labels,
        survey_values,
        title="Survey Score Distribution",
        x_label="Survey score",
        y_label="Count",
        color="#E76F51",
    )

    eda_report_path = write_eda_report(
        metadata,
        ratings,
        reviews,
        tag_catalog,
        tag_counts,
        survey,
        glmer_scores,
        tagdl_scores,
    )
    dataset_report_path = write_dataset_description_report_ko(
        metadata,
        ratings,
        reviews,
        tag_catalog,
        tag_counts,
        survey,
        glmer_scores,
        tagdl_scores,
    )

    rating_movie_head = concentration_stats(rating_movie_counts)

    return {
        "source_id": "genome_2021",
        "source_name": "Genome 2021 Movie Dataset",
        "output_dir": OUTPUT_DIR,
        "eda_report_path": eda_report_path,
        "dataset_report_path": dataset_report_path,
        "movie_ids": set(metadata["movie_titles"].keys()),
        "movie_count": len(metadata["movie_titles"]),
        "rated_movie_count": len(ratings["ratings_per_movie"]),
        "ratings_total": ratings["ratings_total"],
        "user_count": len(ratings["ratings_per_user"]),
        "tag_total": tag_counts["total_tag_applications"],
        "tagging_user_count": 0,
        "tagged_movie_count": len(tag_counts["tag_applications_per_movie"]),
        "review_total": reviews["review_total"],
        "survey_answer_total": survey["answer_total"],
        "score_rows_total": glmer_scores["row_total"] + tagdl_scores["row_total"],
        "has_timestamps": False,
        "item_key_name": "item_id",
        "user_key_name": "user_id",
        "head_movie_share_top1": rating_movie_head[0][3] if rating_movie_head else 0.0,
        "head_user_share_top10": 0.0,
    }
