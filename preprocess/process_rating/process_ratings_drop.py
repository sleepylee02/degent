#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_INPUT_PATH = REPO_ROOT / "data" / "ratings_drop.csv"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "ratings_drop_processed.jsonl"
DEFAULT_VALIDATION_REPORT_PATH = SCRIPT_DIR / "validation_report.json"
DEFAULT_BAD_ROWS_PATH = SCRIPT_DIR / "bad_rows.csv"
REQUIRED_COLUMNS = ["userId", "movieId", "rating", "ratedAt"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert ratings_drop.csv into user-level chronological JSONL histories."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="Path to ratings_drop.csv")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Path to output JSONL")
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
        help="Path to write invalid input rows",
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


def validate_header(fieldnames: list[str] | None) -> None:
    if fieldnames is None:
        raise ValueError("Input CSV is missing a header row.")
    if fieldnames != REQUIRED_COLUMNS:
        raise ValueError(f"Unexpected input columns: {fieldnames}. Expected {REQUIRED_COLUMNS}.")


def coerce_row(raw_row: dict[str, str], row_number: int) -> tuple[dict[str, object] | None, str | None]:
    try:
        user_id = int(raw_row["userId"])
        movie_id = int(raw_row["movieId"])
        rating = float(raw_row["rating"])
        rated_at = raw_row["ratedAt"].strip()
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"parse_error: {exc}"

    if rated_at == "":
        return None, "ratedAt is blank"

    return {
        "rowNumber": row_number,
        "userId": user_id,
        "movieId": movie_id,
        "rating": rating,
        "ratedAt": rated_at,
    }, None


def build_user_record(user_id: int, ratings: list[dict[str, object]]) -> dict[str, object]:
    first_rated_at = str(ratings[0]["ratedAt"])
    last_rated_at = str(ratings[-1]["ratedAt"])
    return {
        "userId": user_id,
        "ratings": [
            {
                "ratedAt": str(rating["ratedAt"]),
                "movieId": int(rating["movieId"]),
                "rating": float(rating["rating"]),
            }
            for rating in ratings
        ],
        "ratingCount": len(ratings),
        "firstRatedAt": first_rated_at,
        "lastRatedAt": last_rated_at,
    }


def write_bad_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_file", "rowNumber", "userId", "movieId", "rating", "ratedAt", "issue"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_validation_report(
    path: Path,
    *,
    input_rows: int,
    output_users: int,
    rating_count_sum: int,
    invalid_rows: int,
    min_rating_count: int,
    max_rating_count: int,
    first_user_id: int | None,
    last_user_id: int | None,
) -> None:
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "ratings_input": input_rows,
            "users_output": output_users,
            "rating_count_sum": rating_count_sum,
            "invalid_rows": invalid_rows,
            "min_rating_count_per_user": min_rating_count,
            "max_rating_count_per_user": max_rating_count,
            "first_user_id": first_user_id,
            "last_user_id": last_user_id,
        },
        "checks": {
            "rating_count_sum_matches_input_minus_invalid": rating_count_sum == (input_rows - invalid_rows),
            "users_output_positive": output_users > 0,
        },
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    args = parse_args()
    validate_input(args.input)
    clear_output_files([args.output, args.validation_report, args.bad_rows])

    args.output.parent.mkdir(parents=True, exist_ok=True)

    input_rows = 0
    output_users = 0
    rating_count_sum = 0
    invalid_rows: list[dict[str, object]] = []
    current_user_id: int | None = None
    current_ratings: list[dict[str, object]] = []
    min_rating_count: int | None = None
    max_rating_count = 0
    first_user_id: int | None = None
    last_user_id: int | None = None
    previous_sort_key: tuple[int, str, int] | None = None

    with (
        args.input.open("r", encoding="utf-8", newline="") as input_handle,
        args.output.open("w", encoding="utf-8", newline="") as output_handle,
    ):
        reader = csv.DictReader(input_handle)
        validate_header(reader.fieldnames)

        for row_number, raw_row in enumerate(reader, start=2):
            input_rows += 1
            row, issue = coerce_row(raw_row, row_number)
            if issue is not None:
                invalid_rows.append(
                    {
                        "source_file": args.input.name,
                        "rowNumber": row_number,
                        "userId": raw_row.get("userId", ""),
                        "movieId": raw_row.get("movieId", ""),
                        "rating": raw_row.get("rating", ""),
                        "ratedAt": raw_row.get("ratedAt", ""),
                        "issue": issue,
                    }
                )
                continue

            user_id = int(row["userId"])
            sort_key = (user_id, str(row["ratedAt"]), int(row["movieId"]))
            if previous_sort_key is not None and sort_key < previous_sort_key:
                raise ValueError(
                    "Input ratings_drop.csv must already be sorted by "
                    "userId, ratedAt, movieId for streaming JSONL generation."
                )
            previous_sort_key = sort_key

            if current_user_id is None:
                current_user_id = user_id
                first_user_id = user_id

            if user_id != current_user_id:
                user_record = build_user_record(current_user_id, current_ratings)
                output_handle.write(json.dumps(user_record, ensure_ascii=False) + "\n")
                output_users += 1
                rating_count_sum += user_record["ratingCount"]
                min_rating_count = (
                    user_record["ratingCount"]
                    if min_rating_count is None
                    else min(min_rating_count, user_record["ratingCount"])
                )
                max_rating_count = max(max_rating_count, user_record["ratingCount"])
                last_user_id = current_user_id
                current_user_id = user_id
                current_ratings = []

            current_ratings.append(row)

        if current_user_id is not None:
            user_record = build_user_record(current_user_id, current_ratings)
            output_handle.write(json.dumps(user_record, ensure_ascii=False) + "\n")
            output_users += 1
            rating_count_sum += user_record["ratingCount"]
            min_rating_count = (
                user_record["ratingCount"]
                if min_rating_count is None
                else min(min_rating_count, user_record["ratingCount"])
            )
            max_rating_count = max(max_rating_count, user_record["ratingCount"])
            last_user_id = current_user_id

    write_bad_rows(args.bad_rows, invalid_rows)
    write_validation_report(
        args.validation_report,
        input_rows=input_rows,
        output_users=output_users,
        rating_count_sum=rating_count_sum,
        invalid_rows=len(invalid_rows),
        min_rating_count=min_rating_count or 0,
        max_rating_count=max_rating_count,
        first_user_id=first_user_id,
        last_user_id=last_user_id,
    )

    print(
        "[done] wrote "
        f"{args.output} "
        f"and validation artifacts to {args.validation_report} / {args.bad_rows} "
        f"(ratings_input={input_rows}, users_output={output_users}, invalid_rows={len(invalid_rows)})",
        flush=True,
    )


if __name__ == "__main__":
    main()
