from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import time
import warnings

import numpy as np

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
    Interest,
    InterestState,
    group_rows_by_user,
    load_interest_state,
    load_online_embedding_rows,
    make_empty_interest_state,
    save_interest_state,
    state_path_for_user,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consume refit requests, cluster active online embeddings, and replace user interest states."
    )
    parser.add_argument("--run-id", type=str, default=None, help="Experiment run id. Defaults to latest train run.")
    parser.add_argument("--hash-inputs", action="store_true", help="Compute SHA256 for input artifact files.")
    parser.add_argument(
        "--hash-limit-mb",
        type=int,
        default=100,
        help="Max file size for SHA256 hashing. Use -1 for no limit.",
    )
    parser.add_argument("--embeddings", type=Path, default=Path("outputs/stream/online_embeddings.npz"))
    parser.add_argument("--refit-requests", type=Path, default=Path("outputs/stream/refit_requests.jsonl"))
    parser.add_argument("--interest-state-dir", type=Path, default=Path("outputs/stream/interest_states"))
    parser.add_argument("--refit-events", type=Path, default=Path("outputs/stream/refit_events.jsonl"))
    parser.add_argument("--cluster-backend", choices=["auto", "gpu", "cpu"], default="auto")
    parser.add_argument("--refit-min-events", type=int, default=20)
    parser.add_argument("--min-cluster-size", type=int, default=10)
    parser.add_argument("--cluster-dim", type=int, default=10)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--user-id", type=int, default=None, help="Only process one user from open requests.")
    parser.add_argument("--limit-users", type=int, default=None, help="Process at most N users from open requests.")
    return parser.parse_args()


def resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_open_refit_requests(path: Path, *, user_id: int | None = None) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}

    open_requests: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("status") != "open":
                continue
            record_user_id = int(record["userId"])
            if user_id is not None and record_user_id != int(user_id):
                continue
            open_requests[record_user_id] = record
    return open_requests


def choose_backend(requested: str) -> tuple[str, str | None]:
    if requested == "cpu":
        return "cpu", None

    try:
        import cuml  # noqa: F401

        return "gpu", None
    except Exception as exc:
        if requested == "gpu":
            raise RuntimeError("Requested GPU backend, but RAPIDS cuML is not importable.") from exc
        return "cpu", f"cuml_unavailable: {exc.__class__.__name__}: {exc}"


def to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "to_numpy"):
        return np.asarray(value.to_numpy())
    try:
        import cupy as cp

        if isinstance(value, cp.ndarray):
            return cp.asnumpy(value)
    except Exception:
        pass
    return np.asarray(value)


def labels_to_interest_vectors(
    embeddings: np.ndarray,
    labels: np.ndarray,
    *,
    backend: str,
) -> tuple[list[Interest], dict[str, Any]]:
    labels = labels.astype(np.int64)
    unique_clusters = sorted(set(labels.tolist()) - {-1})
    timestamp = local_timestamp()

    interests: list[Interest] = []
    cluster_sizes: dict[int, int] = {}
    for new_interest_id, cluster_id in enumerate(unique_clusters):
        mask = labels == cluster_id
        cluster_sizes[int(cluster_id)] = int(mask.sum())
        vector = embeddings[mask].mean(axis=0).astype(float).tolist()
        interests.append(
            Interest(
                interest_id=new_interest_id,
                vector=vector,
                assigned_count=int(mask.sum()),
                created_at=timestamp,
                updated_at=timestamp,
                source=f"cluster_refit:{backend}:cluster_{cluster_id}",
            )
        )

    fallback_used = False
    if not interests:
        fallback_used = True
        vector = embeddings.mean(axis=0).astype(float).tolist()
        interests.append(
            Interest(
                interest_id=0,
                vector=vector,
                assigned_count=int(len(embeddings)),
                created_at=timestamp,
                updated_at=timestamp,
                source=f"cluster_refit:{backend}:mean_fallback_all_noise",
            )
        )

    label_counts = {str(int(label)): int((labels == label).sum()) for label in sorted(set(labels.tolist()))}
    summary = {
        "nClusters": len(unique_clusters),
        "interestCount": len(interests),
        "noiseCount": int((labels == -1).sum()),
        "fallbackUsed": fallback_used,
        "labelCounts": label_counts,
        "clusterSizes": {str(key): value for key, value in cluster_sizes.items()},
    }
    return interests, summary


def refit_cpu(
    embeddings: np.ndarray,
    *,
    min_cluster_size: int,
    cluster_dim: int,
    random_state: int,
) -> tuple[list[Interest], dict[str, Any]]:
    import hdbscan
    import umap

    n_samples = int(embeddings.shape[0])
    if n_samples < max(2, min_cluster_size):
        labels = np.full(n_samples, -1, dtype=np.int64)
        interests, summary = labels_to_interest_vectors(embeddings, labels, backend="cpu")
        summary.update({"backend": "cpu", "reducedDim": None, "reason": "insufficient_samples_mean_fallback"})
        return interests, summary

    reduced_dim = min(cluster_dim, max(2, n_samples - 2))
    n_neighbors = min(15, max(2, n_samples - 1))

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_jobs value 1 overridden", category=UserWarning)
        reducer = umap.UMAP(
            n_components=reduced_dim,
            n_neighbors=n_neighbors,
            random_state=random_state,
            verbose=False,
        )
        z_cluster = reducer.fit_transform(embeddings)

    labels = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(z_cluster)
    interests, summary = labels_to_interest_vectors(embeddings, labels, backend="cpu")
    summary.update({"backend": "cpu", "reducedDim": int(reduced_dim), "nNeighbors": int(n_neighbors)})
    return interests, summary


def refit_gpu(
    embeddings: np.ndarray,
    *,
    min_cluster_size: int,
    cluster_dim: int,
    random_state: int,
) -> tuple[list[Interest], dict[str, Any]]:
    from cuml.cluster import HDBSCAN
    from cuml.manifold import UMAP

    n_samples = int(embeddings.shape[0])
    if n_samples < max(2, min_cluster_size):
        labels = np.full(n_samples, -1, dtype=np.int64)
        interests, summary = labels_to_interest_vectors(embeddings, labels, backend="gpu")
        summary.update({"backend": "gpu", "reducedDim": None, "reason": "insufficient_samples_mean_fallback"})
        return interests, summary

    reduced_dim = min(cluster_dim, max(2, n_samples - 2))
    n_neighbors = min(15, max(2, n_samples - 1))
    reducer = UMAP(
        n_components=reduced_dim,
        n_neighbors=n_neighbors,
        random_state=random_state,
        verbose=False,
    )
    z_cluster = reducer.fit_transform(embeddings)
    labels = HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(z_cluster)
    labels_np = to_numpy(labels).astype(np.int64)
    interests, summary = labels_to_interest_vectors(embeddings, labels_np, backend="gpu")
    summary.update({"backend": "gpu", "reducedDim": int(reduced_dim), "nNeighbors": int(n_neighbors)})
    return interests, summary


def run_refit(
    embeddings: np.ndarray,
    *,
    backend: str,
    min_cluster_size: int,
    cluster_dim: int,
    random_state: int,
) -> tuple[list[Interest], dict[str, Any]]:
    if backend == "gpu":
        return refit_gpu(
            embeddings,
            min_cluster_size=min_cluster_size,
            cluster_dim=cluster_dim,
            random_state=random_state,
        )
    return refit_cpu(
        embeddings,
        min_cluster_size=min_cluster_size,
        cluster_dim=cluster_dim,
        random_state=random_state,
    )


def update_state_after_refit(
    state: InterestState,
    *,
    interests: list[Interest],
    active_raw_event_ids: list[int],
) -> None:
    state.interests = interests
    state.pending_raw_event_ids = []
    state.processed_raw_event_ids = sorted(set(int(value) for value in active_raw_event_ids))
    state.assigned_since_last_refit = 0
    state.outlier_since_last_refit = 0
    state.refit_required = False
    state.refit_request_open = False
    state.refit_reasons = []
    state.last_assigned_at = None
    state.updated_at = local_timestamp()


if __name__ == "__main__":
    args = parse_args()
    ROOT = Path(__file__).resolve().parents[2]
    OUTPUTS_DIR = ROOT / "outputs"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = resolve_model_run_id(OUTPUTS_DIR, args.run_id, prefer_latest=True)
    run_dir = ensure_experiment_run(ROOT, run_id)
    logger, log_path = setup_run_logging("cluster_refit", OUTPUTS_DIR)

    embeddings_path = resolve_path(ROOT, args.embeddings)
    requests_path = resolve_path(ROOT, args.refit_requests)
    interest_state_dir = resolve_path(ROOT, args.interest_state_dir)
    refit_events_path = resolve_path(ROOT, args.refit_events)
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024

    selected_backend, fallback_reason = choose_backend(args.cluster_backend)
    logger.info("Experiment run id: %s", run_id)
    logger.info("Experiment metadata directory: %s", run_dir)
    logger.info("Online embeddings input: %s", embeddings_path)
    logger.info("Refit requests input: %s", requests_path)
    logger.info("Interest state directory: %s", interest_state_dir)
    logger.info("Refit events output: %s", refit_events_path)
    logger.info("Cluster backend requested=%s selected=%s fallback_reason=%s", args.cluster_backend, selected_backend, fallback_reason)

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(ROOT),
            "stages": {
                "cluster_refit": {
                    "command": command_line(),
                    "log": file_metadata(log_path, root=ROOT),
                    "inputs": {
                        "online_embeddings": file_metadata(
                            embeddings_path,
                            root=ROOT,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                        "refit_requests": file_metadata(
                            requests_path,
                            root=ROOT,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "refit_config": {
                        "interest_state_dir": relative_or_absolute(ROOT, interest_state_dir),
                        "refit_events": relative_or_absolute(ROOT, refit_events_path),
                        "cluster_backend_requested": args.cluster_backend,
                        "cluster_backend_selected": selected_backend,
                        "backend_fallback_reason": fallback_reason,
                        "refit_min_events": args.refit_min_events,
                        "min_cluster_size": args.min_cluster_size,
                        "cluster_dim": args.cluster_dim,
                        "random_state": args.random_state,
                        "user_id": args.user_id,
                        "limit_users": args.limit_users,
                    },
                }
            },
        },
    )

    open_requests = load_open_refit_requests(requests_path, user_id=args.user_id)
    request_user_ids = sorted(open_requests)
    if args.limit_users is not None:
        request_user_ids = request_user_ids[: args.limit_users]
    logger.info("Open refit requests: %d selected=%d", len(open_requests), len(request_user_ids))

    rows, embedding_dim = load_online_embedding_rows(embeddings_path)
    grouped_rows = group_rows_by_user(rows)
    logger.info("Loaded active online embedding rows: %d users=%d dim=%d", len(rows), len(grouped_rows), embedding_dim)

    refit_events: list[dict[str, Any]] = []
    user_summaries: list[dict[str, Any]] = []
    refit_count = 0
    skipped_count = 0
    t0 = time.time()

    for user_id in request_user_ids:
        request = open_requests[user_id]
        user_rows = grouped_rows.get(user_id, [])
        state_path = state_path_for_user(interest_state_dir, user_id)
        state = load_interest_state(state_path)
        if state is None:
            state = make_empty_interest_state(user_id, embedding_dim)
        if state.embedding_dim != embedding_dim:
            raise ValueError(f"Embedding dim mismatch for user {user_id}: {state.embedding_dim} != {embedding_dim}")

        base_event = {
            "recordedAt": local_timestamp(),
            "runId": run_id,
            "userId": user_id,
            "request": request,
            "statePath": relative_or_absolute(ROOT, state_path),
            "backendRequested": args.cluster_backend,
            "backendSelected": selected_backend,
            "backendFallbackReason": fallback_reason,
            "activeEmbeddingRows": len(user_rows),
        }

        if len(user_rows) < args.refit_min_events:
            skipped_count += 1
            event = {
                **base_event,
                "status": "skipped",
                "reason": "insufficient_active_embeddings",
                "refitMinEvents": args.refit_min_events,
            }
            refit_events.append(event)
            user_summaries.append(event)
            logger.info("Skipped refit: %s", event)
            continue

        user_embeddings = np.stack([row["embedding"] for row in user_rows]).astype(np.float32)
        active_raw_event_ids = [int(row["rawEventId"]) for row in user_rows]
        user_start = time.time()
        interests, cluster_summary = run_refit(
            user_embeddings,
            backend=selected_backend,
            min_cluster_size=args.min_cluster_size,
            cluster_dim=args.cluster_dim,
            random_state=args.random_state,
        )
        elapsed = time.time() - user_start
        update_state_after_refit(state, interests=interests, active_raw_event_ids=active_raw_event_ids)
        save_interest_state(state, state_path)
        refit_count += 1

        event = {
            **base_event,
            "status": "closed",
            "refitElapsedSec": elapsed,
            "interestCount": len(interests),
            "processedRawEventCount": len(state.processed_raw_event_ids),
            "clusterSummary": cluster_summary,
        }
        refit_events.append(event)
        user_summaries.append(event)
        logger.info("Closed refit request: %s", event)

    if refit_events:
        append_jsonl(refit_events_path, refit_events)

    elapsed_total = time.time() - t0
    metric_record = {
        "stage": "cluster_refit",
        "open_requests": len(open_requests),
        "selected_requests": len(request_user_ids),
        "refit_count": refit_count,
        "skipped_count": skipped_count,
        "cluster_backend_requested": args.cluster_backend,
        "cluster_backend_selected": selected_backend,
        "backend_fallback_reason": fallback_reason,
        "elapsed_sec": elapsed_total,
        "embedding_dim": embedding_dim,
    }
    append_metric(run_dir, metric_record)

    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "cluster_refit": {
                    "data_summary": {
                        "active_embedding_rows": len(rows),
                        "active_embedding_users": len(grouped_rows),
                        "user_summaries": user_summaries,
                    },
                    "outputs": {
                        "refit_events": file_metadata(
                            refit_events_path,
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
