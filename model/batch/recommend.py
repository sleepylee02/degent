from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np
import torch

from model.common.runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    resolve_model_run_id,
    setup_run_logging,
    update_experiment_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score items with clustered user interest vectors and export recommendations."
    )
    parser.add_argument("--interests", type=Path, default=Path("outputs/user_interests.npz"))
    parser.add_argument("--checkpoint", type=Path, default=Path("outputs/sasrec_cl.pt"))
    parser.add_argument("--item2idx", type=Path, default=Path("outputs/item2idx.json"))
    parser.add_argument("--movies", type=Path, default=Path("data/movies_processed_drop.csv"))
    parser.add_argument("--ratings", type=Path, default=Path("data/ratings_drop_processed.jsonl"))
    parser.add_argument("--output-csv", type=Path, default=Path("outputs/recommendations.csv"))
    parser.add_argument("--output-npz", type=Path, default=Path("outputs/recommendations.npz"))
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--user-id",
        type=int,
        action="append",
        default=None,
        help="Limit export to one user. Can be passed multiple times.",
    )
    parser.add_argument("--include-seen", action="store_true", help="Do not filter already positive-rated items.")
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="Use cosine-like normalized dot product instead of raw SASRec dot product.",
    )
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--hash-inputs", action="store_true")
    parser.add_argument("--hash-limit-mb", type=int, default=100)
    return parser.parse_args()


def resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def load_item2idx(path: Path) -> tuple[dict[int, int], dict[int, int]]:
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    item2idx = {int(movie_id): int(idx) for movie_id, idx in raw.items()}
    idx2item = {idx: movie_id for movie_id, idx in item2idx.items()}
    return item2idx, idx2item


def load_item_embeddings(checkpoint_path: Path, *, normalize: bool) -> np.ndarray:
    state_dict = torch.load(checkpoint_path, map_location="cpu")
    if "item_emb.weight" not in state_dict:
        raise KeyError(f"{checkpoint_path} does not contain item_emb.weight")

    embeddings = state_dict["item_emb.weight"].detach().cpu().numpy().astype(np.float32)
    if normalize:
        embeddings = l2_normalize(embeddings)
    return embeddings


def l2_normalize(values: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.maximum(norms, eps)


def load_interest_vectors(
    interests_path: Path,
    *,
    target_user_ids: set[int] | None,
    normalize: bool,
) -> dict[int, list[tuple[int, np.ndarray]]]:
    data = np.load(interests_path)
    required = {"iv_user_ids", "iv_cluster_ids", "interest_vectors"}
    missing = required - set(data.files)
    if missing:
        raise KeyError(f"{interests_path} is missing required arrays: {sorted(missing)}")

    user_ids = data["iv_user_ids"].astype(np.int64)
    cluster_ids = data["iv_cluster_ids"].astype(np.int64)
    vectors = data["interest_vectors"].astype(np.float32)
    if vectors.ndim != 2:
        raise ValueError(f"interest_vectors must be 2D, got shape={vectors.shape}")

    finite_mask = np.isfinite(vectors).all(axis=1)
    if target_user_ids is not None:
        finite_mask &= np.isin(user_ids, list(target_user_ids))

    if normalize:
        vectors = l2_normalize(vectors)

    by_user: dict[int, list[tuple[int, np.ndarray]]] = defaultdict(list)
    for uid, cid, vector in zip(user_ids[finite_mask], cluster_ids[finite_mask], vectors[finite_mask]):
        by_user[int(uid)].append((int(cid), vector))

    return dict(by_user)


def load_movie_metadata(path: Path) -> dict[int, dict[str, str]]:
    metadata: dict[int, dict[str, str]] = {}
    if not path.exists():
        return metadata

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                movie_id = int(row["movieId"])
            except (KeyError, TypeError, ValueError):
                continue
            metadata[movie_id] = {
                "title": row.get("title", ""),
                "releaseYear": row.get("releaseYear", ""),
                "genres": row.get("genres", ""),
                "ratingAvg": row.get("ratingAvg", ""),
                "ratingCount": row.get("ratingCount", ""),
            }
    return metadata


def positive_movie_ids(ratings: list[dict]) -> set[int]:
    if not ratings:
        return set()

    values = np.array([float(row["rating"]) for row in ratings], dtype=np.float32)
    std = float(values.std())
    if std <= 1e-8:
        return set()

    z_scores = (values - float(values.mean())) / (std + 1e-8)
    return {
        int(row["movieId"])
        for row, z_score in zip(ratings, z_scores)
        if z_score > 0
    }


def load_seen_positive_items(path: Path, target_user_ids: set[int]) -> dict[int, set[int]]:
    seen = {uid: set() for uid in target_user_ids}
    if not path.exists() or not target_user_ids:
        return seen

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            uid = int(entry["userId"])
            if uid not in target_user_ids:
                continue
            seen[uid] = positive_movie_ids(entry.get("ratings", []))
    return seen


def build_candidate_index(
    item_embeddings: np.ndarray,
    idx2item: dict[int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    candidate_indices = np.array(
        [idx for idx in sorted(idx2item) if 0 < idx < item_embeddings.shape[0]],
        dtype=np.int64,
    )
    candidate_movie_ids = np.array([idx2item[int(idx)] for idx in candidate_indices], dtype=np.int64)
    candidate_vectors = item_embeddings[candidate_indices]
    return candidate_indices, candidate_movie_ids, candidate_vectors


def top_recommendations_for_user(
    user_id: int,
    interests: list[tuple[int, np.ndarray]],
    candidate_indices: np.ndarray,
    candidate_movie_ids: np.ndarray,
    candidate_vectors: np.ndarray,
    *,
    top_k: int,
    seen_movie_ids: set[int],
) -> tuple[list[dict], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    cluster_ids = np.array([cid for cid, _ in interests], dtype=np.int64)
    interest_matrix = np.stack([vector for _, vector in interests], axis=0)

    scores_by_cluster = interest_matrix @ candidate_vectors.T
    best_cluster_offsets = scores_by_cluster.argmax(axis=0)
    best_scores = scores_by_cluster[best_cluster_offsets, np.arange(candidate_vectors.shape[0])]

    valid_mask = np.isfinite(best_scores)
    if seen_movie_ids:
        valid_mask &= ~np.isin(candidate_movie_ids, list(seen_movie_ids))

    valid_offsets = np.flatnonzero(valid_mask)
    if valid_offsets.size == 0:
        return [], cluster_ids, np.array([], dtype=np.int64), np.array([], dtype=np.int64), np.array([], dtype=np.float32)

    k = min(top_k, valid_offsets.size)
    top_offsets_unsorted = valid_offsets[np.argpartition(best_scores[valid_offsets], -k)[-k:]]
    top_offsets = top_offsets_unsorted[np.argsort(best_scores[top_offsets_unsorted])[::-1]]

    rows: list[dict] = []
    for rank, offset in enumerate(top_offsets, start=1):
        best_cluster_offset = int(best_cluster_offsets[offset])
        rows.append(
            {
                "userId": int(user_id),
                "rank": rank,
                "movieId": int(candidate_movie_ids[offset]),
                "itemIdx": int(candidate_indices[offset]),
                "score": float(best_scores[offset]),
                "bestClusterId": int(cluster_ids[best_cluster_offset]),
                "clusterScores": scores_by_cluster[:, offset].astype(np.float32).tolist(),
            }
        )

    return (
        rows,
        cluster_ids,
        candidate_movie_ids[top_offsets],
        candidate_indices[top_offsets],
        best_scores[top_offsets].astype(np.float32),
    )


def write_recommendations_csv(
    path: Path,
    rows: Iterable[dict],
    movie_metadata: dict[int, dict[str, str]],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "userId",
        "rank",
        "movieId",
        "itemIdx",
        "title",
        "releaseYear",
        "genres",
        "ratingAvg",
        "ratingCount",
        "score",
        "bestClusterId",
        "clusterScores",
    ]

    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            meta = movie_metadata.get(int(row["movieId"]), {})
            out = {
                **row,
                "title": meta.get("title", ""),
                "releaseYear": meta.get("releaseYear", ""),
                "genres": meta.get("genres", ""),
                "ratingAvg": meta.get("ratingAvg", ""),
                "ratingCount": meta.get("ratingCount", ""),
                "clusterScores": json.dumps(row["clusterScores"], separators=(",", ":")),
            }
            writer.writerow(out)
            count += 1
    return count


def write_recommendations_npz(
    path: Path,
    rows: list[dict],
    *,
    top_k: int,
    include_seen: bool,
    normalize: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    max_clusters = max((len(row["clusterScores"]) for row in rows), default=0)
    cluster_scores = np.full((len(rows), max_clusters), np.nan, dtype=np.float32)
    for idx, row in enumerate(rows):
        values = np.array(row["clusterScores"], dtype=np.float32)
        cluster_scores[idx, : values.shape[0]] = values

    np.savez(
        path,
        user_ids=np.array([row["userId"] for row in rows], dtype=np.int64),
        ranks=np.array([row["rank"] for row in rows], dtype=np.int64),
        movie_ids=np.array([row["movieId"] for row in rows], dtype=np.int64),
        item_indices=np.array([row["itemIdx"] for row in rows], dtype=np.int64),
        scores=np.array([row["score"] for row in rows], dtype=np.float32),
        best_cluster_ids=np.array([row["bestClusterId"] for row in rows], dtype=np.int64),
        cluster_scores=cluster_scores,
        top_k=np.array(top_k, dtype=np.int64),
        include_seen=np.array(include_seen, dtype=np.bool_),
        normalized=np.array(normalize, dtype=np.bool_),
    )


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    outputs_dir = root / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    run_id = resolve_model_run_id(outputs_dir, args.run_id, prefer_latest=True)
    run_dir = ensure_experiment_run(root, run_id)
    logger, log_path = setup_run_logging("recommend", outputs_dir)
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024

    interests_path = resolve_path(root, args.interests)
    checkpoint_path = resolve_path(root, args.checkpoint)
    item2idx_path = resolve_path(root, args.item2idx)
    movies_path = resolve_path(root, args.movies)
    ratings_path = resolve_path(root, args.ratings)
    output_csv_path = resolve_path(root, args.output_csv)
    output_npz_path = resolve_path(root, args.output_npz)
    target_user_ids = set(args.user_id) if args.user_id else None

    if args.top_k <= 0:
        raise ValueError("--top-k must be positive")

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(root),
            "stages": {
                "recommend": {
                    "command": command_line(),
                    "log": file_metadata(log_path, root=root),
                    "inputs": {
                        "interests": file_metadata(
                            interests_path,
                            root=root,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "checkpoint": file_metadata(
                            checkpoint_path,
                            root=root,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "item2idx": file_metadata(
                            item2idx_path,
                            root=root,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "movies": file_metadata(
                            movies_path,
                            root=root,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "ratings": file_metadata(
                            ratings_path,
                            root=root,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "recommend_config": {
                        "top_k": args.top_k,
                        "user_ids": sorted(target_user_ids) if target_user_ids else None,
                        "include_seen": args.include_seen,
                        "normalize": args.normalize,
                    },
                }
            },
        },
    )

    logger.info("Experiment run id: %s", run_id)
    logger.info("Loading interests: %s", interests_path)
    interests_by_user = load_interest_vectors(
        interests_path,
        target_user_ids=target_user_ids,
        normalize=args.normalize,
    )
    if not interests_by_user:
        raise SystemExit("No interest vectors available for recommendation.")

    item2idx, idx2item = load_item2idx(item2idx_path)
    item_embeddings = load_item_embeddings(checkpoint_path, normalize=args.normalize)
    candidate_indices, candidate_movie_ids, candidate_vectors = build_candidate_index(item_embeddings, idx2item)
    movie_metadata = load_movie_metadata(movies_path)

    users = sorted(interests_by_user)
    seen_by_user = (
        {uid: set() for uid in users}
        if args.include_seen
        else load_seen_positive_items(ratings_path, set(users))
    )

    all_rows: list[dict] = []
    users_with_recommendations = 0
    for user_id in users:
        rows, _, _, _, _ = top_recommendations_for_user(
            user_id,
            interests_by_user[user_id],
            candidate_indices,
            candidate_movie_ids,
            candidate_vectors,
            top_k=args.top_k,
            seen_movie_ids=seen_by_user.get(user_id, set()),
        )
        all_rows.extend(rows)
        if rows:
            users_with_recommendations += 1

    csv_rows = write_recommendations_csv(output_csv_path, all_rows, movie_metadata)
    write_recommendations_npz(
        output_npz_path,
        all_rows,
        top_k=args.top_k,
        include_seen=args.include_seen,
        normalize=args.normalize,
    )

    metric_record = {
        "stage": "recommend",
        "users_requested": len(users),
        "users_with_recommendations": users_with_recommendations,
        "recommendation_rows": csv_rows,
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
                "recommend": {
                    "data_summary": {
                        "interest_users": len(users),
                        "interest_vectors": sum(len(v) for v in interests_by_user.values()),
                        "candidate_items": int(candidate_movie_ids.shape[0]),
                        "item2idx_items": int(len(item2idx)),
                        "item_embedding_rows": int(item_embeddings.shape[0]),
                    },
                    "outputs": {
                        "recommendations_csv": file_metadata(
                            output_csv_path,
                            root=root,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "recommendations_npz": file_metadata(
                            output_npz_path,
                            root=root,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "metrics": file_metadata(
                            run_dir / "metrics.jsonl",
                            root=root,
                            include_sha256=True,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "summary_metrics": metric_record,
                }
            },
        },
    )
    logger.info(
        "Saved recommendations | csv=%s rows=%d npz=%s users=%d",
        output_csv_path,
        csv_rows,
        output_npz_path,
        users_with_recommendations,
    )


if __name__ == "__main__":
    main()
