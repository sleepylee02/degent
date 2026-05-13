from __future__ import annotations

from typing import Any, NamedTuple
import warnings

import numpy as np


class ClusterOutput(NamedTuple):
    z_cluster: np.ndarray   # (N, reduced_dim)
    labels: np.ndarray      # (N,)  -1 = noise
    reduced_dim: int
    n_neighbors: int


def probe_gpu_backend() -> None:
    import cuml  # noqa: F401
    import cupy as cp

    if int(cp.cuda.runtime.getDeviceCount()) <= 0:
        raise RuntimeError("No CUDA devices visible.")


def choose_backend(requested: str) -> tuple[str, str | None]:
    if requested == "cpu":
        return "cpu", None
    try:
        probe_gpu_backend()
        return "gpu", None
    except Exception as exc:
        reason = f"gpu_unavailable: {exc.__class__.__name__}: {exc}"
        if requested == "gpu":
            raise RuntimeError("Requested GPU backend unavailable.") from exc
        return "cpu", reason


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


def _safe_umap_params(n_samples: int, cluster_dim: int) -> tuple[int, int]:
    reduced_dim = min(cluster_dim, max(2, n_samples - 2))
    n_neighbors = min(15, max(2, n_samples - 1))
    return reduced_dim, n_neighbors


def cluster_cpu(
    embeddings: np.ndarray,
    *,
    min_cluster_size: int,
    cluster_dim: int,
    random_state: int,
) -> ClusterOutput:
    import hdbscan
    import umap as umap_lib

    n_samples = int(embeddings.shape[0])
    reduced_dim, n_neighbors = _safe_umap_params(n_samples, cluster_dim)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_jobs value 1 overridden", category=UserWarning)
        reducer = umap_lib.UMAP(
            n_components=reduced_dim,
            n_neighbors=n_neighbors,
            random_state=random_state,
            verbose=False,
        )
        z_cluster = np.asarray(reducer.fit_transform(embeddings))

    labels = np.asarray(
        hdbscan.HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(z_cluster),
        dtype=np.int64,
    )
    return ClusterOutput(z_cluster=z_cluster, labels=labels,
                         reduced_dim=reduced_dim, n_neighbors=n_neighbors)


def cluster_gpu(
    embeddings: np.ndarray,
    *,
    min_cluster_size: int,
    cluster_dim: int,
    random_state: int,
) -> ClusterOutput:
    from cuml.cluster import HDBSCAN
    from cuml.manifold import UMAP

    n_samples = int(embeddings.shape[0])
    reduced_dim, n_neighbors = _safe_umap_params(n_samples, cluster_dim)

    reducer = UMAP(
        n_components=reduced_dim,
        n_neighbors=n_neighbors,
        random_state=random_state,
        verbose=False,
    )
    z_cluster = to_numpy(reducer.fit_transform(embeddings))
    labels = to_numpy(
        HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(z_cluster)
    ).astype(np.int64)
    return ClusterOutput(z_cluster=z_cluster, labels=labels,
                         reduced_dim=reduced_dim, n_neighbors=n_neighbors)


def cluster_embeddings(
    embeddings: np.ndarray,
    *,
    backend: str,
    min_cluster_size: int,
    cluster_dim: int,
    random_state: int,
) -> ClusterOutput:
    n_samples = int(embeddings.shape[0])
    if n_samples < max(2, min_cluster_size):
        reduced_dim, n_neighbors = _safe_umap_params(n_samples, cluster_dim)
        labels = np.full(n_samples, -1, dtype=np.int64)
        z_dummy = embeddings[:, :min(reduced_dim, embeddings.shape[1])]
        return ClusterOutput(z_cluster=z_dummy, labels=labels,
                             reduced_dim=reduced_dim, n_neighbors=n_neighbors)

    if backend == "gpu":
        return cluster_gpu(embeddings, min_cluster_size=min_cluster_size,
                           cluster_dim=cluster_dim, random_state=random_state)
    return cluster_cpu(embeddings, min_cluster_size=min_cluster_size,
                       cluster_dim=cluster_dim, random_state=random_state)


def cluster_with_fallback(
    embeddings: np.ndarray,
    *,
    requested_backend: str,
    selected_backend: str,
    fallback_reason: str | None,
    min_cluster_size: int,
    cluster_dim: int,
    random_state: int,
    logger: Any,
) -> tuple[ClusterOutput, str, str | None]:
    try:
        result = cluster_embeddings(
            embeddings,
            backend=selected_backend,
            min_cluster_size=min_cluster_size,
            cluster_dim=cluster_dim,
            random_state=random_state,
        )
        return result, selected_backend, fallback_reason
    except Exception as exc:
        if requested_backend != "auto" or selected_backend != "gpu":
            raise
        runtime_reason = f"gpu_cluster_failed: {exc.__class__.__name__}: {exc}"
        logger.warning("GPU clustering failed; falling back to CPU: %s", runtime_reason)
        result = cluster_embeddings(
            embeddings,
            backend="cpu",
            min_cluster_size=min_cluster_size,
            cluster_dim=cluster_dim,
            random_state=random_state,
        )
        return result, "cpu", runtime_reason


def compute_interest_vectors(embeddings: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """클러스터별 centroid를 원본 임베딩 공간(128D)에서 계산. returns (K, d)."""
    unique_clusters = sorted(set(labels.tolist()) - {-1})
    if not unique_clusters:
        return np.zeros((0, embeddings.shape[1]), dtype=np.float32)
    return np.stack([embeddings[labels == k].mean(axis=0) for k in unique_clusters])


def top_genres_for_cluster(
    movie_ids: np.ndarray,
    genre_map_idx: dict[int, list[int]],
    all_genres: list[str],
    n: int = 5,
) -> list[dict]:
    """클러스터에 속한 movie_ids의 장르 빈도를 세어 상위 n개를 반환. returns [{"genre": str, "count": int}]."""
    counts: dict[int, int] = {}
    for mid in movie_ids:
        for idx in genre_map_idx.get(int(mid), []):
            counts[idx] = counts.get(idx, 0) + 1
    top = sorted(counts, key=lambda k: counts[k], reverse=True)[:n]
    return [{"genre": all_genres[i], "count": counts[i]} for i in top]
