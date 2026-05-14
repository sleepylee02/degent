from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from model.batch.recommend import (
    build_candidate_index,
    load_item2idx,
    load_item_embeddings,
    load_movie_metadata,
    top_recommendations_for_user,
)
from model.common.runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    local_timestamp,
    resolve_model_run_id,
    setup_run_logging,
    update_experiment_manifest,
)
from model.stream.interest_assign import (
    InterestState,
    load_interest_state,
    state_path_for_user as interest_state_path,
)
from model.stream.state import OnlineUserState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score items against streaming interest vectors and export online recommendations."
    )
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--hash-inputs", action="store_true")
    parser.add_argument("--hash-limit-mb", type=int, default=100)
    parser.add_argument("--interest-state-dir", type=Path, default=Path("outputs/stream/interest_states"))
    parser.add_argument("--user-state-dir", type=Path, default=Path("outputs/stream/user_states"))
    parser.add_argument("--checkpoint", type=Path, default=Path("outputs/sasrec_cl.pt"))
    parser.add_argument("--item2idx", type=Path, default=Path("outputs/item2idx.json"))
    parser.add_argument("--movies", type=Path, default=Path("data/movies_processed_drop.csv"))
    parser.add_argument("--output-jsonl", type=Path, default=Path("outputs/stream/stream_recommendations.jsonl"))
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--user-id",
        type=int,
        action="append",
        default=None,
        help="Limit to specific user ids. Can be passed multiple times.",
    )
    parser.add_argument("--include-seen", action="store_true")
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="Use cosine-normalized dot product instead of raw dot product.",
    )
    return parser.parse_args()


def resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def discover_user_ids(interest_state_dir: Path) -> list[int]:
    if not interest_state_dir.exists():
        return []
    return sorted(
        int(p.stem)
        for p in interest_state_dir.iterdir()
        if p.suffix == ".json" and p.stem.isdigit()
    )


def load_interest_vectors_from_stream(
    interest_state_dir: Path,
    user_ids: list[int],
    *,
    normalize: bool,
) -> tuple[dict[int, list[tuple[int, np.ndarray]]], dict[int, str]]:
    """Read InterestState JSON files and convert to the format top_recommendations_for_user expects.

    Returns (by_user, skip_reasons) where skip_reasons maps skipped user_ids to reason strings.
    """
    by_user: dict[int, list[tuple[int, np.ndarray]]] = {}
    skip_reasons: dict[int, str] = {}

    for user_id in user_ids:
        path = interest_state_path(interest_state_dir, user_id)
        state: InterestState | None = load_interest_state(path)

        if state is None:
            skip_reasons[user_id] = "no_interest_state_file"
            continue
        if not state.interests:
            skip_reasons[user_id] = "empty_interests"
            continue

        pairs: list[tuple[int, np.ndarray]] = []
        for interest in state.interests:
            vec = np.array(interest.vector, dtype=np.float32)
            if not np.isfinite(vec).all():
                continue
            if normalize:
                norm = float(np.linalg.norm(vec))
                if norm > 1e-12:
                    vec = vec / norm
            pairs.append((interest.interest_id, vec))

        if not pairs:
            skip_reasons[user_id] = "all_interest_vectors_non_finite"
            continue

        by_user[user_id] = pairs

    return by_user, skip_reasons


def load_seen_from_user_states(
    user_state_dir: Path,
    user_ids: list[int],
) -> dict[int, set[int]]:
    """Read OnlineUserState JSON files and collect positive movie_ids as the seen set."""
    seen: dict[int, set[int]] = {uid: set() for uid in user_ids}

    for user_id in user_ids:
        path = user_state_dir / f"{user_id}.json"
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            state = OnlineUserState.from_dict(raw)
        except Exception:
            continue

        for event in state.positive_events or []:
            seen[user_id].add(event.movie_id)

    return seen


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    args = parse_args()
    ROOT = Path(__file__).resolve().parents[2]
    OUTPUTS_DIR = ROOT / "outputs"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = resolve_model_run_id(OUTPUTS_DIR, args.run_id, prefer_latest=True)
    run_dir = ensure_experiment_run(ROOT, run_id)
    logger, log_path = setup_run_logging("recommend_online", OUTPUTS_DIR)
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024

    interest_state_dir = resolve_path(ROOT, args.interest_state_dir)
    user_state_dir = resolve_path(ROOT, args.user_state_dir)
    checkpoint_path = resolve_path(ROOT, args.checkpoint)
    item2idx_path = resolve_path(ROOT, args.item2idx)
    movies_path = resolve_path(ROOT, args.movies)
    output_jsonl_path = resolve_path(ROOT, args.output_jsonl)

    if args.top_k <= 0:
        raise ValueError("--top-k must be positive")

    target_user_ids: list[int] | None = args.user_id
    if target_user_ids is None:
        target_user_ids = discover_user_ids(interest_state_dir)
    if not target_user_ids:
        raise SystemExit("No interest state files found. Run interest_assign or cluster_refit first.")

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(ROOT),
            "stages": {
                "recommend_online": {
                    "command": command_line(),
                    "log": file_metadata(log_path, root=ROOT),
                    "inputs": {
                        "checkpoint": file_metadata(
                            checkpoint_path,
                            root=ROOT,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "item2idx": file_metadata(
                            item2idx_path,
                            root=ROOT,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "recommend_config": {
                        "interest_state_dir": str(interest_state_dir),
                        "user_state_dir": str(user_state_dir),
                        "output_jsonl": str(output_jsonl_path),
                        "top_k": args.top_k,
                        "user_ids": sorted(target_user_ids),
                        "include_seen": args.include_seen,
                        "normalize": args.normalize,
                    },
                }
            },
        },
    )

    logger.info("Experiment run id: %s", run_id)
    logger.info("Interest state dir: %s", interest_state_dir)
    logger.info("User state dir: %s", user_state_dir)
    logger.info("Target users: %d", len(target_user_ids))

    interests_by_user, skip_reasons = load_interest_vectors_from_stream(
        interest_state_dir,
        target_user_ids,
        normalize=args.normalize,
    )
    for uid, reason in skip_reasons.items():
        logger.info("Skipped user %d: %s", uid, reason)

    eligible_users = sorted(interests_by_user)
    if not eligible_users:
        raise SystemExit("No users with valid interest vectors. Run cluster_refit first.")

    item2idx, idx2item = load_item2idx(item2idx_path)
    item_embeddings = load_item_embeddings(checkpoint_path, normalize=args.normalize)
    candidate_indices, candidate_movie_ids, candidate_vectors = build_candidate_index(item_embeddings, idx2item)
    movie_metadata = load_movie_metadata(movies_path)
    logger.info(
        "Loaded checkpoint: %d items | candidates: %d",
        item_embeddings.shape[0],
        candidate_movie_ids.shape[0],
    )

    seen_by_user = (
        {uid: set() for uid in eligible_users}
        if args.include_seen
        else load_seen_from_user_states(user_state_dir, eligible_users)
    )

    now = local_timestamp()
    output_records: list[dict[str, Any]] = []
    users_with_recommendations = 0

    for user_id in eligible_users:
        rows, _, _, _, _ = top_recommendations_for_user(
            user_id,
            interests_by_user[user_id],
            candidate_indices,
            candidate_movie_ids,
            candidate_vectors,
            top_k=args.top_k,
            seen_movie_ids=seen_by_user.get(user_id, set()),
        )
        if not rows:
            logger.info("No recommendations for user %d", user_id)
            continue

        users_with_recommendations += 1
        for row in rows:
            meta = movie_metadata.get(int(row["movieId"]), {})
            output_records.append(
                {
                    "recordedAt": now,
                    "runId": run_id,
                    "userId": row["userId"],
                    "rank": row["rank"],
                    "movieId": row["movieId"],
                    "itemIdx": row["itemIdx"],
                    "title": meta.get("title", ""),
                    "releaseYear": meta.get("releaseYear", ""),
                    "genres": meta.get("genres", ""),
                    "ratingAvg": meta.get("ratingAvg", ""),
                    "ratingCount": meta.get("ratingCount", ""),
                    "score": row["score"],
                    "bestClusterId": row["bestClusterId"],
                    "clusterScores": row["clusterScores"],
                    "normalize": args.normalize,
                    "includeSeen": args.include_seen,
                    "topK": args.top_k,
                }
            )
        logger.info(
            "Recommended user %d: %d items | top score=%.4f",
            user_id,
            len(rows),
            rows[0]["score"],
        )

    append_jsonl(output_jsonl_path, output_records)
    logger.info("Saved %d recommendation rows → %s", len(output_records), output_jsonl_path)

    metric_record = {
        "stage": "recommend_online",
        "target_users": len(target_user_ids),
        "eligible_users": len(eligible_users),
        "skipped_users": len(skip_reasons),
        "users_with_recommendations": users_with_recommendations,
        "recommendation_rows": len(output_records),
        "candidate_items": int(candidate_movie_ids.shape[0]),
        "top_k": args.top_k,
        "include_seen": args.include_seen,
        "normalize": args.normalize,
    }
    append_metric(run_dir, metric_record)

    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "recommend_online": {
                    "data_summary": {
                        "target_users": len(target_user_ids),
                        "eligible_users": len(eligible_users),
                        "skipped_users": len(skip_reasons),
                        "skip_reasons": {str(uid): reason for uid, reason in skip_reasons.items()},
                    },
                    "outputs": {
                        "stream_recommendations": file_metadata(
                            output_jsonl_path,
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "metrics": file_metadata(
                            run_dir / "metrics.jsonl",
                            root=ROOT,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "summary_metrics": metric_record,
                }
            },
        },
    )
