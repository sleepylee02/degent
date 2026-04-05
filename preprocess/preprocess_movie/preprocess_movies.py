#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import yaml

STRICT_YEAR_SUFFIX_PATTERN = r"\((\d{4})\)\s*$"
RELAXED_YEAR_SUFFIX_PATTERN = r"\((\d{4})\)+\s*$"
STRICT_YEAR_SUFFIX_REGEX = re.compile(STRICT_YEAR_SUFFIX_PATTERN)
RELAXED_YEAR_SUFFIX_REGEX = re.compile(RELAXED_YEAR_SUFFIX_PATTERN)
NO_GENRES_LISTED = "(no genres listed)"

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "ml-32m" / "raw"
DEFAULT_MOVIES_PATH = DEFAULT_RAW_DIR / "movies.csv"
DEFAULT_TAGS_PATH = DEFAULT_RAW_DIR / "tags.csv"
DEFAULT_LINKS_PATH = DEFAULT_RAW_DIR / "links.csv"
DEFAULT_RATINGS_PATH = DEFAULT_RAW_DIR / "ratings.csv"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "movies_processed.csv"
DEFAULT_VALIDATION_REPORT_PATH = SCRIPT_DIR / "validation_report.json"
DEFAULT_BAD_ROWS_PATH = SCRIPT_DIR / "bad_rows.csv"
DEFAULT_PROCESSED_SCHEMA_PATH = REPO_ROOT / "schemas" / "ml32m" / "processed" / "movies_processed.v2.schema.yaml"
ISSUE_FRAME_SCHEMA = {
    "source_file": pl.String,
    "movieId": pl.String,
    "issue": pl.String,
    "severity": pl.String,
    "details": pl.String,
}
SCALAR_TYPE_TO_POLARS = {
    "int64": pl.Int64,
    "float64": pl.Float64,
    "string": pl.String,
    "bool": pl.Boolean,
    "boolean": pl.Boolean,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess MovieLens 32M movie metadata into a single movies_processed.csv file."
    )
    parser.add_argument("--movies", type=Path, default=DEFAULT_MOVIES_PATH, help="Path to movies.csv")
    parser.add_argument("--tags", type=Path, default=DEFAULT_TAGS_PATH, help="Path to tags.csv")
    parser.add_argument("--links", type=Path, default=DEFAULT_LINKS_PATH, help="Path to links.csv")
    parser.add_argument("--ratings", type=Path, default=DEFAULT_RATINGS_PATH, help="Path to ratings.csv")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Path to output CSV")
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
        help="Path to write per-issue CSV records",
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


def empty_issue_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=ISSUE_FRAME_SCHEMA)


def load_schema(schema_path: Path) -> dict[str, object]:
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = yaml.safe_load(handle)

    if not isinstance(schema, dict):
        raise ValueError(f"Schema file must contain a mapping: {schema_path}")

    return schema


def schema_columns(schema: dict[str, object]) -> list[dict[str, object]]:
    columns = schema.get("columns")
    if not isinstance(columns, list):
        raise ValueError(f"Schema is missing a valid columns list: {schema.get('name', '<unknown>')}")
    return columns


def schema_column_polars_dtype(column: dict[str, object]) -> pl.DataType:
    physical_type = str(column.get("physical_type", ""))
    logical_type = str(column.get("logical_type", ""))

    if physical_type == "json_string":
        return pl.String

    type_name = physical_type or logical_type
    if type_name not in SCALAR_TYPE_TO_POLARS:
        raise ValueError(f"Unsupported schema type: {type_name} for column {column.get('name')}")

    return SCALAR_TYPE_TO_POLARS[type_name]


def schema_source_name(schema_path: Path, schema: dict[str, object]) -> str:
    schema_name = str(schema.get("name", schema_path.stem))
    schema_version = str(schema.get("version", "unknown"))
    return f"{schema_path.name} ({schema_name}:{schema_version})"


def parse_genres(raw_genres: str | None) -> list[str]:
    if raw_genres is None:
        return []
    genres = [genre.strip() for genre in raw_genres.split("|") if genre.strip()]
    return [genre for genre in genres if genre != NO_GENRES_LISTED]


def parse_title_year(raw_title: str | None) -> tuple[str | None, int | None, str | None]:
    if raw_title is None:
        return None, None, None

    title = raw_title.strip()

    strict_match = STRICT_YEAR_SUFFIX_REGEX.search(title)
    if strict_match:
        return title[: strict_match.start()].rstrip(), int(strict_match.group(1)), "strict"

    relaxed_match = RELAXED_YEAR_SUFFIX_REGEX.search(title)
    if relaxed_match:
        return title[: relaxed_match.start()].rstrip(), int(relaxed_match.group(1)), "relaxed"

    return title, None, None


def parse_title(raw_title: str | None) -> str | None:
    title, _, _ = parse_title_year(raw_title)
    return title


def is_blank_title(title: str | None) -> bool:
    return title is None or title.strip() == ""


def parse_year(raw_title: str | None) -> int | None:
    _, year, _ = parse_title_year(raw_title)
    return year


def used_relaxed_year_parser(raw_title: str | None) -> bool:
    _, _, mode = parse_title_year(raw_title)
    return mode == "relaxed"


def ensure_list(values: object) -> list[str]:
    if values is None:
        return []
    if isinstance(values, list):
        return values
    if isinstance(values, pl.Series):
        return values.to_list()
    return values if isinstance(values, list) else []


def json_array(values: list[str] | None) -> str:
    return json.dumps(ensure_list(values), ensure_ascii=False)


def make_issue_frame(
    frame: pl.DataFrame,
    *,
    source_file: str,
    issue: str,
    severity: str,
    details: str | pl.Expr,
) -> pl.DataFrame:
    details_expr = pl.lit(details) if isinstance(details, str) else details
    return frame.select(
        pl.lit(source_file).alias("source_file"),
        pl.col("movieId").cast(pl.Utf8).alias("movieId"),
        pl.lit(issue).alias("issue"),
        pl.lit(severity).alias("severity"),
        details_expr.alias("details"),
    )


def make_dataset_issue_frame(
    *,
    source_file: str,
    issue: str,
    severity: str,
    details: str,
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "source_file": [source_file],
            "movieId": [""],
            "issue": [issue],
            "severity": [severity],
            "details": [details],
        },
        schema=ISSUE_FRAME_SCHEMA,
    )


def build_movies_frame(movies_path: Path) -> pl.DataFrame:
    movies = pl.read_csv(
        movies_path,
        schema_overrides={
            "movieId": pl.Int64,
            "title": pl.String,
            "genres": pl.String,
        },
    ).rename({"title": "raw_title", "genres": "raw_genres"})

    parsed = movies.with_columns(
        pl.col("raw_title").map_elements(parse_title, return_dtype=pl.String).alias("title"),
        pl.col("raw_title").map_elements(parse_year, return_dtype=pl.Int64).alias("releaseYear"),
        pl.col("raw_title").map_elements(used_relaxed_year_parser, return_dtype=pl.Boolean).alias(
            "usedRelaxedYearParser"
        ),
        pl.col("raw_genres").map_elements(parse_genres, return_dtype=pl.List(pl.String)).alias("genres"),
    )

    return parsed.with_columns(
        pl.col("title").map_elements(is_blank_title, return_dtype=pl.Boolean, skip_nulls=False).alias(
            "hasBlankTitleAfterParse"
        ),
    )


def build_tags_frame(tags_path: Path) -> pl.DataFrame:
    tags = pl.read_csv(
        tags_path,
        columns=["movieId", "tag"],
        schema_overrides={
            "movieId": pl.Int64,
            "tag": pl.String,
        },
    )

    cleaned_tags = tags.with_columns(
        pl.col("tag").fill_null("").str.strip_chars().alias("tag"),
    ).filter(pl.col("tag") != "")

    return (
        cleaned_tags.group_by("movieId", maintain_order=True)
        .agg(pl.col("tag").unique(maintain_order=True).alias("tag"))
        .with_columns(pl.col("tag").list.len().alias("tagCount"))
    )


def build_links_frame(links_path: Path) -> pl.DataFrame:
    return pl.read_csv(
        links_path,
        schema_overrides={
            "movieId": pl.Int64,
            "imdbId": pl.String,
            "tmdbId": pl.String,
        },
    ).with_columns(
        pl.col("imdbId").cast(pl.Int64, strict=False).alias("imdbId"),
        pl.col("tmdbId").cast(pl.Int64, strict=False).alias("tmdbId"),
        pl.lit(True).alias("hasLinksRow"),
    )


def build_ratings_frame(ratings_path: Path) -> pl.DataFrame:
    ratings = pl.read_csv(
        ratings_path,
        columns=["movieId", "rating"],
        schema_overrides={
            "movieId": pl.Int64,
            "rating": pl.Float64,
        },
    )

    return ratings.group_by("movieId").agg(
        pl.col("rating").mean().round(2).alias("ratingAvg"),
        pl.len().alias("ratingCount"),
        pl.lit(True).alias("hasRatingsRow"),
    )


def build_processed_frame(
    movies: pl.DataFrame,
    tags: pl.DataFrame,
    links: pl.DataFrame,
    ratings: pl.DataFrame,
) -> pl.DataFrame:
    processed = (
        movies.join(tags, on="movieId", how="left")
        .join(links, on="movieId", how="left")
        .join(ratings, on="movieId", how="left")
    )

    return processed.with_columns(
        pl.col("tag").map_elements(ensure_list, return_dtype=pl.List(pl.String)).alias("tag"),
        pl.col("tagCount").fill_null(0).cast(pl.Int64).alias("tagCount"),
        pl.col("hasLinksRow").fill_null(False).alias("hasLinksRow"),
        pl.col("ratingCount").fill_null(0).cast(pl.Int64).alias("ratingCount"),
        pl.col("hasRatingsRow").fill_null(False).alias("hasRatingsRow"),
    )


def build_stats(
    processed: pl.DataFrame,
    source_movie_ids: set[int],
    tag_movie_ids: set[int],
    link_movie_ids: set[int],
    rating_movie_ids: set[int],
    blank_title_dropped: int,
) -> dict[str, int]:
    return {
        "movies": processed.height,
        "movies_dropped_blank_title": blank_title_dropped,
        "titles_without_year": processed.filter(pl.col("releaseYear").is_null()).height,
        "titles_parsed_with_relaxed_year": processed.filter(pl.col("usedRelaxedYearParser")).height,
        "movies_without_tags": processed.filter(pl.col("tagCount") == 0).height,
        "movies_without_links_row": processed.filter(~pl.col("hasLinksRow")).height,
        "movies_without_imdb_id": processed.filter(pl.col("hasLinksRow") & pl.col("imdbId").is_null()).height,
        "movies_without_tmdb_id": processed.filter(pl.col("hasLinksRow") & pl.col("tmdbId").is_null()).height,
        "movies_without_genres": processed.filter(pl.col("genres").list.len() == 0).height,
        "movies_without_ratings": processed.filter(pl.col("ratingCount") == 0).height,
        "orphan_tag_movie_ids": len(tag_movie_ids - source_movie_ids),
        "orphan_link_movie_ids": len(link_movie_ids - source_movie_ids),
        "orphan_rating_movie_ids": len(rating_movie_ids - source_movie_ids),
    }


def build_issues(
    processed: pl.DataFrame,
    *,
    movies_source_name: str,
    tags_source_name: str,
    links_source_name: str,
    ratings_source_name: str,
    source_movie_ids: set[int],
    tag_movie_ids: set[int],
    link_movie_ids: set[int],
    rating_movie_ids: set[int],
    blank_title_rows: pl.DataFrame | None = None,
) -> pl.DataFrame:
    issue_frames = [
        make_issue_frame(
            processed.filter(pl.col("usedRelaxedYearParser")),
            source_file=movies_source_name,
            issue="relaxed_title_year_match",
            severity="warning",
            details=pl.format("title={}", pl.col("raw_title")),
        ),
        make_issue_frame(
            processed.filter(pl.col("releaseYear").is_null()),
            source_file=movies_source_name,
            issue="title_without_year",
            severity="warning",
            details=pl.format("title={}", pl.col("raw_title")),
        ),
        make_issue_frame(
            processed.filter(pl.col("genres").list.len() == 0),
            source_file=movies_source_name,
            issue="missing_genres",
            severity="warning",
            details=pl.format("raw_genres={}", pl.col("raw_genres").fill_null("")),
        ),
        make_issue_frame(
            processed.filter(pl.col("tagCount") == 0),
            source_file=tags_source_name,
            issue="missing_tags",
            severity="info",
            details="No tags found for this movieId in tags.csv",
        ),
        make_issue_frame(
            processed.filter(~pl.col("hasLinksRow")),
            source_file=links_source_name,
            issue="missing_links_row",
            severity="warning",
            details="No matching row found in links.csv",
        ),
        make_issue_frame(
            processed.filter(pl.col("hasLinksRow") & pl.col("imdbId").is_null()),
            source_file=links_source_name,
            issue="missing_imdb_id",
            severity="warning",
            details="imdbId is empty",
        ),
        make_issue_frame(
            processed.filter(pl.col("hasLinksRow") & pl.col("tmdbId").is_null()),
            source_file=links_source_name,
            issue="missing_tmdb_id",
            severity="warning",
            details="tmdbId is empty",
        ),
        make_issue_frame(
            processed.filter(pl.col("ratingCount") == 0),
            source_file=ratings_source_name,
            issue="missing_ratings",
            severity="info",
            details="No ratings found for this movieId in ratings.csv",
        ),
    ]

    if blank_title_rows is not None and blank_title_rows.height > 0:
        issue_frames.insert(
            0,
            make_issue_frame(
                blank_title_rows,
                source_file=movies_source_name,
                issue="blank_title_after_parse",
                severity="warning",
                details=pl.format("raw_title={}", pl.col("raw_title")),
            ),
        )

    orphan_tag_ids = sorted(tag_movie_ids - source_movie_ids)
    if orphan_tag_ids:
        issue_frames.append(
            pl.DataFrame(
                {
                    "source_file": [tags_source_name] * len(orphan_tag_ids),
                    "movieId": [str(movie_id) for movie_id in orphan_tag_ids],
                    "issue": ["orphan_tags_movie_id"] * len(orphan_tag_ids),
                    "severity": ["warning"] * len(orphan_tag_ids),
                    "details": ["movieId exists in tags.csv but not in movies.csv"] * len(orphan_tag_ids),
                }
            )
        )

    orphan_link_ids = sorted(link_movie_ids - source_movie_ids)
    if orphan_link_ids:
        issue_frames.append(
            pl.DataFrame(
                {
                    "source_file": [links_source_name] * len(orphan_link_ids),
                    "movieId": [str(movie_id) for movie_id in orphan_link_ids],
                    "issue": ["orphan_links_movie_id"] * len(orphan_link_ids),
                    "severity": ["warning"] * len(orphan_link_ids),
                    "details": ["movieId exists in links.csv but not in movies.csv"] * len(orphan_link_ids),
                }
            )
        )

    orphan_rating_ids = sorted(rating_movie_ids - source_movie_ids)
    if orphan_rating_ids:
        issue_frames.append(
            pl.DataFrame(
                {
                    "source_file": [ratings_source_name] * len(orphan_rating_ids),
                    "movieId": [str(movie_id) for movie_id in orphan_rating_ids],
                    "issue": ["orphan_ratings_movie_id"] * len(orphan_rating_ids),
                    "severity": ["warning"] * len(orphan_rating_ids),
                    "details": ["movieId exists in ratings.csv but not in movies.csv"] * len(orphan_rating_ids),
                }
            )
        )

    return pl.concat(issue_frames, how="vertical")


def build_output_frame(processed: pl.DataFrame) -> pl.DataFrame:
    return processed.select(
        pl.col("movieId"),
        pl.col("title"),
        pl.col("releaseYear"),
        pl.col("genres").map_elements(json_array, return_dtype=pl.String).alias("genres"),
        pl.col("tag").map_elements(json_array, return_dtype=pl.String, skip_nulls=False).alias("tag"),
        pl.col("tagCount"),
        pl.col("ratingAvg"),
        pl.col("ratingCount"),
        pl.col("imdbId"),
        pl.col("tmdbId"),
    )


def build_processed_schema_issues(
    output_frame: pl.DataFrame,
    *,
    processed_schema: dict[str, object],
    processed_schema_path: Path,
) -> pl.DataFrame:
    schema_label = schema_source_name(processed_schema_path, processed_schema)
    issue_frames: list[pl.DataFrame] = []
    expected_columns = [str(column["name"]) for column in schema_columns(processed_schema)]
    actual_columns = output_frame.columns

    missing_columns = [column for column in expected_columns if column not in actual_columns]
    if missing_columns:
        issue_frames.append(
            make_dataset_issue_frame(
                source_file=schema_label,
                issue="schema_missing_columns",
                severity="error",
                details="missing_columns=" + ",".join(missing_columns),
            )
        )

    unexpected_columns = [column for column in actual_columns if column not in expected_columns]
    if unexpected_columns:
        issue_frames.append(
            make_dataset_issue_frame(
                source_file=schema_label,
                issue="schema_unexpected_columns",
                severity="error",
                details="unexpected_columns=" + ",".join(unexpected_columns),
            )
        )

    if actual_columns != expected_columns:
        issue_frames.append(
            make_dataset_issue_frame(
                source_file=schema_label,
                issue="schema_column_order_mismatch",
                severity="error",
                details=f"expected={expected_columns}; actual={actual_columns}",
            )
        )

    for column in schema_columns(processed_schema):
        column_name = str(column["name"])
        if column_name not in output_frame.columns:
            continue

        expected_dtype = schema_column_polars_dtype(column)
        actual_dtype = output_frame.schema[column_name]
        if actual_dtype != expected_dtype:
            issue_frames.append(
                make_dataset_issue_frame(
                    source_file=schema_label,
                    issue="schema_dtype_mismatch",
                    severity="error",
                    details=f"column={column_name}; expected={expected_dtype}; actual={actual_dtype}",
                )
            )

        if not bool(column.get("nullable", True)):
            issue_frames.append(
                make_issue_frame(
                    output_frame.filter(pl.col(column_name).is_null()),
                    source_file=schema_label,
                    issue="schema_non_nullable_violation",
                    severity="error",
                    details=pl.lit(f"column={column_name}"),
                )
            )

    for constraint in processed_schema.get("constraints", []):
        if not isinstance(constraint, dict):
            continue

        constraint_type = str(constraint.get("type", ""))
        columns = [str(column) for column in constraint.get("columns", [])]
        if not columns or any(column not in output_frame.columns for column in columns):
            continue

        if constraint_type == "unique":
            duplicates = output_frame.group_by(columns).len().filter(pl.col("len") > 1).drop("len")
            if duplicates.height > 0:
                issue_frames.append(
                    make_issue_frame(
                        output_frame.join(duplicates, on=columns, how="inner"),
                        source_file=schema_label,
                        issue="schema_unique_violation",
                        severity="error",
                        details=pl.lit("columns=" + ",".join(columns)),
                    )
                )

        elif constraint_type == "non_negative":
            for column_name in columns:
                issue_frames.append(
                    make_issue_frame(
                        output_frame.filter(pl.col(column_name).is_not_null() & (pl.col(column_name) < 0)),
                        source_file=schema_label,
                        issue="schema_non_negative_violation",
                        severity="error",
                        details=pl.format("column={}; value={}", pl.lit(column_name), pl.col(column_name).cast(pl.String)),
                    )
                )

        elif constraint_type == "range":
            min_value = constraint.get("min_value")
            max_value = constraint.get("max_value")
            for column_name in columns:
                issue_frames.append(
                    make_issue_frame(
                        output_frame.filter(
                            pl.col(column_name).is_not_null()
                            & ((pl.col(column_name) < min_value) | (pl.col(column_name) > max_value))
                        ),
                        source_file=schema_label,
                        issue="schema_range_violation",
                        severity="error",
                        details=pl.format(
                            "column={}; min={}; max={}; value={}",
                            pl.lit(column_name),
                            pl.lit(min_value),
                            pl.lit(max_value),
                            pl.col(column_name).cast(pl.String),
                        ),
                    )
                )

    non_empty_issue_frames = [frame for frame in issue_frames if frame.height > 0]
    if not non_empty_issue_frames:
        return empty_issue_frame()

    return pl.concat(non_empty_issue_frames, how="vertical")


def write_output(output_path: Path, output_frame: pl.DataFrame) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_frame.write_csv(output_path, null_value="")


def write_bad_rows(bad_rows_path: Path, issues: pl.DataFrame) -> None:
    bad_rows_path.parent.mkdir(parents=True, exist_ok=True)
    issues.write_csv(bad_rows_path)


def write_validation_report(
    report_path: Path,
    *,
    issues: pl.DataFrame,
    stats: dict[str, int],
    movies_processed: int,
    unique_movies_in_movies_csv: int,
    unique_movies_with_tags: int,
    unique_movies_with_links: int,
    unique_movies_with_ratings: int,
    processed_schema: dict[str, object],
    processed_schema_path: Path,
    schema_issue_count: int,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)

    issues_summary_rows = (
        issues.group_by("issue")
        .agg(
            pl.len().alias("count"),
            pl.col("movieId").cast(pl.Int64, strict=False).drop_nulls().unique(maintain_order=True).head(20).alias(
                "sample_movie_ids"
            ),
        )
        .sort("issue")
        .to_dicts()
    )

    issues_summary = {
        row["issue"]: {
            "count": row["count"],
            "sample_movie_ids": row["sample_movie_ids"],
        }
        for row in issues_summary_rows
    }

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "movies_processed": movies_processed,
            "unique_movies_in_movies_csv": unique_movies_in_movies_csv,
            "unique_movies_with_tags": unique_movies_with_tags,
            "unique_movies_with_links": unique_movies_with_links,
            "unique_movies_with_ratings": unique_movies_with_ratings,
            "total_issue_records": issues.height,
        },
        "schema_validation": {
            "processed_schema_file": str(processed_schema_path.relative_to(REPO_ROOT)),
            "processed_schema_name": processed_schema.get("name"),
            "processed_schema_version": processed_schema.get("version"),
            "issue_count": schema_issue_count,
        },
        "counts": stats,
        "issues": issues_summary,
    }

    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    args = parse_args()
    validate_inputs([args.movies, args.tags, args.links, args.ratings])
    clear_output_files([args.output, args.validation_report, args.bad_rows])
    processed_schema = load_schema(DEFAULT_PROCESSED_SCHEMA_PATH)

    movies = build_movies_frame(args.movies)
    tags = build_tags_frame(args.tags)
    links = build_links_frame(args.links)
    ratings = build_ratings_frame(args.ratings)

    processed_all = build_processed_frame(movies, tags, links, ratings)
    blank_title_rows = processed_all.filter(pl.col("hasBlankTitleAfterParse"))
    processed = processed_all.filter(~pl.col("hasBlankTitleAfterParse"))
    output_frame = build_output_frame(processed)

    movie_ids = set(movies["movieId"].to_list())
    tag_movie_ids = set(tags["movieId"].to_list())
    link_movie_ids = set(links["movieId"].to_list())
    rating_movie_ids = set(ratings["movieId"].to_list())

    stats = build_stats(
        processed,
        movie_ids,
        tag_movie_ids,
        link_movie_ids,
        rating_movie_ids,
        blank_title_dropped=blank_title_rows.height,
    )
    issues = build_issues(
        processed,
        movies_source_name=args.movies.name,
        tags_source_name=args.tags.name,
        links_source_name=args.links.name,
        ratings_source_name=args.ratings.name,
        source_movie_ids=movie_ids,
        tag_movie_ids=tag_movie_ids,
        link_movie_ids=link_movie_ids,
        rating_movie_ids=rating_movie_ids,
        blank_title_rows=blank_title_rows,
    )
    schema_issues = build_processed_schema_issues(
        output_frame,
        processed_schema=processed_schema,
        processed_schema_path=DEFAULT_PROCESSED_SCHEMA_PATH,
    )
    issues = pl.concat([issues, schema_issues], how="vertical")
    schema_issue_count = schema_issues.height

    write_bad_rows(args.bad_rows, issues)
    write_validation_report(
        args.validation_report,
        issues=issues,
        stats=stats,
        movies_processed=processed.height,
        unique_movies_in_movies_csv=len(movie_ids),
        unique_movies_with_tags=len(tag_movie_ids),
        unique_movies_with_links=len(link_movie_ids),
        unique_movies_with_ratings=len(rating_movie_ids),
        processed_schema=processed_schema,
        processed_schema_path=DEFAULT_PROCESSED_SCHEMA_PATH,
        schema_issue_count=schema_issue_count,
    )

    if schema_issue_count > 0:
        raise SystemExit(
            "Schema validation failed with "
            f"{schema_issue_count} issue(s). "
            f"See {args.validation_report} and {args.bad_rows}."
        )

    write_output(args.output, output_frame)

    print(
        "[done] wrote "
        f"{args.output} "
        f"and validation artifacts to {args.validation_report} / {args.bad_rows} "
        f"(movies={stats['movies']}, "
        f"blank_titles_dropped={stats['movies_dropped_blank_title']}, "
        f"titles_without_year={stats['titles_without_year']}, "
        f"movies_without_tags={stats['movies_without_tags']}, "
        f"movies_without_ratings={stats['movies_without_ratings']}, "
        f"movies_without_links_row={stats['movies_without_links_row']}, "
        f"schema_issues={schema_issue_count})",
        flush=True,
    )


if __name__ == "__main__":
    main()
