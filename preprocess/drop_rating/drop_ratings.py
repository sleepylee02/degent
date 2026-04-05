#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_RATINGS_PATH = REPO_ROOT / "data" / "ml-32m" / "raw" / "ratings.csv"
DEFAULT_MOVIES_KEEP_PATH = REPO_ROOT / "data" / "movies_processed_drop.csv"
DEFAULT_MOVIE_BAD_ROWS_PATH = REPO_ROOT / "preprocess" / "drop_movie" / "bad_rows.csv"
DEFAULT_PREPROCESS_MOVIE_BAD_ROWS_PATH = REPO_ROOT / "preprocess" / "preprocess_movie" / "bad_rows.csv"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "ratings_drop.csv"
DEFAULT_VALIDATION_REPORT_PATH = SCRIPT_DIR / "validation_report.json"
DEFAULT_BAD_ROWS_PATH = SCRIPT_DIR / "bad_rows.csv"
ISSUE_ORDER = [
    "missing_ratings",
    "missing_genres",
    "title_without_year",
]
RATED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drop rating rows whose movieId is not present in movies_processed_drop.csv."
    )
    parser.add_argument("--ratings", type=Path, default=DEFAULT_RATINGS_PATH, help="Path to raw ratings.csv")
    parser.add_argument(
        "--movies-keep",
        type=Path,
        default=DEFAULT_MOVIES_KEEP_PATH,
        help="Path to movies_processed_drop.csv used as the keep set",
    )
    parser.add_argument(
        "--movie-bad-rows",
        type=Path,
        default=DEFAULT_MOVIE_BAD_ROWS_PATH,
        help="Path to drop_movie bad_rows.csv used to attach movie drop reasons",
    )
    parser.add_argument(
        "--preprocess-movie-bad-rows",
        type=Path,
        default=DEFAULT_PREPROCESS_MOVIE_BAD_ROWS_PATH,
        help="Optional path to preprocess_movie bad_rows.csv used as a fallback reason source",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Path to filtered ratings CSV")
    parser.add_argument(
        "--validation-report",
        type=Path,
        default=DEFAULT_VALIDATION_REPORT_PATH,
        help="Path to write validation summary JSON",
    )
    parser.add_argument(
        "--bad-rows",
        type=Path,
        default=DEFAULT_BAD_ROWS_PATH,
        help="Path to write dropped rating rows with movie drop reasons",
    )
    return parser.parse_args()


def validate_inputs(paths: list[Path]) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required input files: " + ", ".join(missing))


def remove_existing_file(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        raise IsADirectoryError(f"Expected file path but found directory: {path}")
    path.unlink()


def clear_output_files(paths: list[Path]) -> None:
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        remove_existing_file(path)


def json_array(values: list[str] | None) -> str:
    if values is None:
        normalized: list[str] = []
    elif isinstance(values, pl.Series):
        normalized = values.to_list()
    else:
        normalized = values
    return json.dumps(normalized, ensure_ascii=False)


def empty_reason_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "movieId": pl.Int64,
            "drop_reasons": pl.List(pl.String),
            "drop_reasons_json": pl.String,
        }
    )


def load_keep_movie_ids(movies_keep_path: Path) -> pl.DataFrame:
    return (
        pl.read_csv(
            movies_keep_path,
            columns=["movieId"],
            schema_overrides={"movieId": pl.Int64},
        )
        .select(pl.col("movieId").unique(maintain_order=True))
        .sort("movieId")
    )


def load_movie_drop_reasons(movie_bad_rows_path: Path) -> pl.DataFrame:
    return (
        pl.read_csv(
            movie_bad_rows_path,
            columns=["movieId", "issue"],
            schema_overrides={
                "movieId": pl.Int64,
                "issue": pl.String,
            },
        )
        .group_by("movieId")
        .agg(pl.col("issue").unique().sort().alias("drop_reasons"))
        .with_columns(
            pl.col("drop_reasons").map_elements(json_array, return_dtype=pl.String).alias("drop_reasons_json")
        )
        .sort("movieId")
    )


def load_optional_reason_frame(path: Path) -> pl.DataFrame:
    if not path.is_file():
        return empty_reason_frame()
    return load_movie_drop_reasons(path)


def scan_ratings(ratings_path: Path) -> pl.LazyFrame:
    return pl.scan_csv(
        ratings_path,
        schema_overrides={
            "userId": pl.Int64,
            "movieId": pl.Int64,
            "rating": pl.Float64,
            "timestamp": pl.Int64,
        },
    )


def rated_at_expr(timestamp_column: str = "timestamp") -> pl.Expr:
    return (
        pl.from_epoch(pl.col(timestamp_column), time_unit="s")
        .dt.replace_time_zone("UTC")
        .dt.strftime(RATED_AT_FORMAT)
    )


def sink_output(path: Path, frame: pl.LazyFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.sink_csv(path)


def write_validation_report(
    report_path: Path,
    *,
    ratings_input: int,
    ratings_output: int,
    unique_movies_in_ratings: int,
    unique_movies_kept: int,
    unique_movies_dropped: int,
    unique_users_in_ratings: int,
    unique_users_kept: int,
    affected_users: int,
    total_bad_rows: int,
    reason_row_counts: dict[str, int],
    issue_combinations: list[dict[str, object]],
) -> None:
    ratings_dropped = ratings_input - ratings_output
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "ratings_input": ratings_input,
            "ratings_output": ratings_output,
            "ratings_dropped": ratings_dropped,
            "drop_ratio": round((ratings_dropped / ratings_input), 6) if ratings_input else 0.0,
            "drop_ratio_percent": round((ratings_dropped / ratings_input) * 100, 4) if ratings_input else 0.0,
            "unique_movies_in_ratings": unique_movies_in_ratings,
            "unique_movies_kept": unique_movies_kept,
            "unique_movies_dropped": unique_movies_dropped,
            "unique_users_in_ratings": unique_users_in_ratings,
            "unique_users_kept": unique_users_kept,
            "affected_users": affected_users,
            "total_bad_rows": total_bad_rows,
        },
        "counts": {
            **reason_row_counts,
        },
        "issue_combinations": issue_combinations,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    args = parse_args()
    validate_inputs([args.ratings, args.movies_keep, args.movie_bad_rows])
    clear_output_files([args.output, args.validation_report, args.bad_rows])

    keep_movie_ids = load_keep_movie_ids(args.movies_keep)
    movie_drop_reasons = load_movie_drop_reasons(args.movie_bad_rows)
    preprocess_movie_drop_reasons = load_optional_reason_frame(args.preprocess_movie_bad_rows)
    fallback_preprocess_reasons = preprocess_movie_drop_reasons.join(
        movie_drop_reasons.select("movieId"),
        on="movieId",
        how="anti",
    )
    combined_drop_reasons = pl.concat([movie_drop_reasons, fallback_preprocess_reasons], how="vertical")
    ratings = scan_ratings(args.ratings)

    keep_movies_lf = keep_movie_ids.lazy()
    movie_drop_reasons_lf = combined_drop_reasons.lazy()

    kept_ratings = ratings.join(keep_movies_lf, on="movieId", how="semi").with_columns(
        rated_at_expr().alias("ratedAt")
    )
    dropped_ratings = ratings.join(keep_movies_lf, on="movieId", how="anti").with_columns(
        rated_at_expr().alias("ratedAt")
    )
    dropped_ratings_with_reasons = dropped_ratings.join(movie_drop_reasons_lf, on="movieId", how="left")

    output_frame = kept_ratings.sort(["userId", "ratedAt", "movieId"]).select(["userId", "movieId", "rating", "ratedAt"])
    bad_rows_frame = dropped_ratings_with_reasons.select(
        pl.lit(args.ratings.name).alias("source_file"),
        pl.col("userId"),
        pl.col("movieId"),
        pl.col("rating"),
        pl.col("ratedAt"),
        pl.col("drop_reasons_json").alias("drop_reasons"),
    )

    sink_output(args.output, output_frame)
    sink_output(args.bad_rows, bad_rows_frame)

    input_summary = ratings.select(
        pl.len().alias("ratings_input"),
        pl.col("movieId").n_unique().alias("unique_movies_in_ratings"),
        pl.col("userId").n_unique().alias("unique_users_in_ratings"),
    ).collect()
    output_summary = kept_ratings.select(
        pl.len().alias("ratings_output"),
        pl.col("movieId").n_unique().alias("unique_movies_kept"),
        pl.col("userId").n_unique().alias("unique_users_kept"),
    ).collect()
    dropped_summary = dropped_ratings.select(
        pl.len().alias("ratings_dropped"),
        pl.col("movieId").n_unique().alias("unique_movies_dropped"),
        pl.col("userId").n_unique().alias("affected_users"),
    ).collect()
    reason_row_counts_frame = dropped_ratings_with_reasons.select(
        pl.col("drop_reasons").list.contains("missing_ratings").sum().alias("rating_rows_with_missing_ratings_movie"),
        pl.col("drop_reasons").list.contains("missing_genres").sum().alias("rating_rows_with_missing_genres_movie"),
        pl.col("drop_reasons")
        .list.contains("title_without_year")
        .sum()
        .alias("rating_rows_with_title_without_year_movie"),
        pl.len().alias("ratings_dropped_union"),
    ).collect()
    issue_combinations_frame = (
        dropped_ratings_with_reasons.group_by("drop_reasons")
        .agg(
            pl.len().alias("ratings_dropped"),
            pl.col("movieId").n_unique().alias("movies"),
        )
        .sort("ratings_dropped", descending=True)
        .collect()
    )

    input_row = input_summary.row(0, named=True)
    output_row = output_summary.row(0, named=True)
    dropped_row = dropped_summary.row(0, named=True)
    reason_row_counts = reason_row_counts_frame.row(0, named=True)
    issue_combinations = [
        {
            "issues": row["drop_reasons"],
            "ratings_dropped": row["ratings_dropped"],
            "movies": row["movies"],
        }
        for row in issue_combinations_frame.iter_rows(named=True)
    ]

    write_validation_report(
        args.validation_report,
        ratings_input=input_row["ratings_input"],
        ratings_output=output_row["ratings_output"],
        unique_movies_in_ratings=input_row["unique_movies_in_ratings"],
        unique_movies_kept=output_row["unique_movies_kept"],
        unique_movies_dropped=dropped_row["unique_movies_dropped"],
        unique_users_in_ratings=input_row["unique_users_in_ratings"],
        unique_users_kept=output_row["unique_users_kept"],
        affected_users=dropped_row["affected_users"],
        total_bad_rows=dropped_row["ratings_dropped"],
        reason_row_counts=reason_row_counts,
        issue_combinations=issue_combinations,
    )

    print(
        "[done] wrote "
        f"{args.output} "
        f"and validation artifacts to {args.validation_report} / {args.bad_rows} "
        f"(ratings_input={input_row['ratings_input']}, ratings_output={output_row['ratings_output']}, "
        f"ratings_dropped={dropped_row['ratings_dropped']})",
        flush=True,
    )


if __name__ == "__main__":
    main()
