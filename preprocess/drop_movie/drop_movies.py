#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_INPUT_PATH = REPO_ROOT / "data" / "movies_processed.csv"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "movies_processed_drop.csv"
DEFAULT_VALIDATION_REPORT_PATH = SCRIPT_DIR / "validation_report.json"
DEFAULT_BAD_ROWS_PATH = SCRIPT_DIR / "bad_rows.csv"

REQUIRED_COLUMNS = [
    "movieId",
    "title",
    "releaseYear",
    "genres",
    "tag",
    "tagCount",
    "ratingAvg",
    "ratingCount",
    "imdbId",
    "tmdbId",
]

ISSUE_ORDER = [
    "missing_ratings",
    "missing_genres",
    "title_without_year",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drop rows from movies_processed.csv using fixed quality rules."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="Path to movies_processed.csv")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Path to filtered output CSV")
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
        help="Path to write dropped rows with reasons",
    )
    return parser.parse_args()


def validate_input(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required input file: {path}")


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


def parse_json_string_list(raw_value: str | None) -> list[str]:
    if raw_value is None or raw_value == "":
        return []

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON list value: {raw_value}") from exc

    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON list value but received: {raw_value}")

    normalized: list[str] = []
    for value in parsed:
        if not isinstance(value, str):
            raise ValueError(f"Expected list[string] JSON value but received: {raw_value}")
        normalized.append(value)

    return normalized


def validate_required_columns(frame: pl.DataFrame, required_columns: list[str]) -> None:
    missing_columns = [column for column in required_columns if column not in frame.columns]
    if missing_columns:
        raise ValueError("Input CSV is missing required columns: " + ", ".join(missing_columns))


def load_input_frame(input_path: Path) -> pl.DataFrame:
    frame = pl.read_csv(
        input_path,
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
    validate_required_columns(frame, REQUIRED_COLUMNS)
    return frame


def build_flagged_frame(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        frame.with_columns(
            pl.col("genres")
            .map_elements(parse_json_string_list, return_dtype=pl.List(pl.String))
            .alias("_parsed_genres"),
        )
        .with_columns(
            (pl.col("ratingCount") == 0).alias("missing_ratings"),
            (pl.col("_parsed_genres").list.len() == 0).alias("missing_genres"),
            pl.col("releaseYear").is_null().alias("title_without_year"),
        )
        .with_columns(
            pl.any_horizontal(*[pl.col(issue) for issue in ISSUE_ORDER]).alias("drop_row"),
        )
    )


def empty_issue_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "source_file": pl.String,
            "movieId": pl.String,
            "issue": pl.String,
            "severity": pl.String,
            "details": pl.String,
        }
    )


def make_issue_frame(
    frame: pl.DataFrame,
    *,
    source_file: str,
    issue: str,
    details: str | pl.Expr,
) -> pl.DataFrame:
    details_expr = pl.lit(details) if isinstance(details, str) else details
    return frame.select(
        pl.lit(source_file).alias("source_file"),
        pl.col("movieId").cast(pl.Utf8).alias("movieId"),
        pl.lit(issue).alias("issue"),
        pl.lit("drop").alias("severity"),
        details_expr.alias("details"),
    )


def build_issues(flagged: pl.DataFrame, *, source_file: str) -> pl.DataFrame:
    dropped = flagged.filter(pl.col("drop_row"))
    issue_frames = []

    rating_issues = dropped.filter(pl.col("missing_ratings"))
    if rating_issues.height > 0:
        issue_frames.append(
            make_issue_frame(
                rating_issues,
                source_file=source_file,
                issue="missing_ratings",
                details="ratingCount == 0",
            )
        )

    genre_issues = dropped.filter(pl.col("missing_genres"))
    if genre_issues.height > 0:
        issue_frames.append(
            make_issue_frame(
                genre_issues,
                source_file=source_file,
                issue="missing_genres",
                details=pl.format("genres={}", pl.col("genres").fill_null("")),
            )
        )

    title_issues = dropped.filter(pl.col("title_without_year"))
    if title_issues.height > 0:
        issue_frames.append(
            make_issue_frame(
                title_issues,
                source_file=source_file,
                issue="title_without_year",
                details=pl.format("title={}", pl.col("title").fill_null("")),
            )
        )

    if not issue_frames:
        return empty_issue_frame()

    return pl.concat(issue_frames, how="vertical")


def build_output_frame(flagged: pl.DataFrame, original_columns: list[str]) -> pl.DataFrame:
    return flagged.filter(~pl.col("drop_row")).select(original_columns)


def build_issue_summary(issues: pl.DataFrame) -> dict[str, dict[str, object]]:
    summary: dict[str, dict[str, object]] = {}
    for issue in ISSUE_ORDER:
        matching = issues.filter(pl.col("issue") == issue)
        summary[issue] = {
            "count": matching.height,
            "sample_movie_ids": matching.select(
                pl.col("movieId").cast(pl.Int64, strict=False).drop_nulls().unique(maintain_order=True).head(20)
            ).to_series().to_list(),
        }
    return summary


def build_issue_combinations(flagged: pl.DataFrame) -> list[dict[str, object]]:
    dropped = flagged.filter(pl.col("drop_row")).select(["movieId", *ISSUE_ORDER])
    combination_counter: Counter[tuple[str, ...]] = Counter()

    for row in dropped.iter_rows(named=True):
        active_issues = tuple(issue for issue in ISSUE_ORDER if row[issue])
        combination_counter[active_issues] += 1

    return [
        {
            "issues": list(issues),
            "count": count,
        }
        for issues, count in sorted(combination_counter.items(), key=lambda item: (len(item[0]), item[0]))
    ]


def write_output(output_path: Path, output_frame: pl.DataFrame) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_frame.write_csv(output_path, null_value="")


def write_bad_rows(bad_rows_path: Path, issues: pl.DataFrame) -> None:
    bad_rows_path.parent.mkdir(parents=True, exist_ok=True)
    issues.write_csv(bad_rows_path)


def write_validation_report(
    report_path: Path,
    *,
    input_rows: int,
    output_rows: int,
    issues: pl.DataFrame,
    issue_combinations: list[dict[str, object]],
) -> None:
    dropped_rows = input_rows - output_rows
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "movies_input": input_rows,
            "movies_output": output_rows,
            "movies_dropped": dropped_rows,
            "drop_ratio": round((dropped_rows / input_rows), 6) if input_rows else 0.0,
            "drop_ratio_percent": round((dropped_rows / input_rows) * 100, 4) if input_rows else 0.0,
            "total_issue_records": issues.height,
        },
        "counts": {
            "missing_ratings": issues.filter(pl.col("issue") == "missing_ratings").height,
            "missing_genres": issues.filter(pl.col("issue") == "missing_genres").height,
            "title_without_year": issues.filter(pl.col("issue") == "title_without_year").height,
            "movies_dropped_union": dropped_rows,
            "movies_kept": output_rows,
        },
        "issues": build_issue_summary(issues),
        "issue_combinations": issue_combinations,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    args = parse_args()
    validate_input(args.input)
    clear_output_files([args.output, args.validation_report, args.bad_rows])

    input_frame = load_input_frame(args.input)
    original_columns = input_frame.columns
    flagged = build_flagged_frame(input_frame)
    output_frame = build_output_frame(flagged, original_columns)
    issues = build_issues(flagged, source_file=args.input.name)
    issue_combinations = build_issue_combinations(flagged)

    write_output(args.output, output_frame)
    write_bad_rows(args.bad_rows, issues)
    write_validation_report(
        args.validation_report,
        input_rows=input_frame.height,
        output_rows=output_frame.height,
        issues=issues,
        issue_combinations=issue_combinations,
    )

    dropped_rows = input_frame.height - output_frame.height
    print(
        "[done] wrote "
        f"{args.output} "
        f"and validation artifacts to {args.validation_report} / {args.bad_rows} "
        f"(movies_input={input_frame.height}, movies_output={output_frame.height}, movies_dropped={dropped_rows})",
        flush=True,
    )


if __name__ == "__main__":
    main()
