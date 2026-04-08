from pathlib import Path
import time

import numpy as np
import umap
import hdbscan
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

from runtime import setup_run_logging


# =====================
# UMAP 차원 축소
# =====================

def reduce_dimensions(embeddings, n_components, random_state=42, logger=None):
    """
    embeddings: (N, d_model) numpy array
    n_components: 축소할 차원 수
    returns: (N, n_components) numpy array
    """
    if logger:
        logger.info("  UMAP start | n_components=%d  shape=%s", n_components, embeddings.shape)
    t0 = time.time()
    reducer = umap.UMAP(n_components=n_components, random_state=random_state, verbose=True)
    result = reducer.fit_transform(embeddings)
    if logger:
        logger.info("  UMAP done  | elapsed=%.1fs", time.time() - t0)
    return result


# =====================
# HDBSCAN 클러스터링
# =====================

def cluster(z, min_cluster_size=10, logger=None):
    """
    z: (N, n_components) numpy array
    returns:
        labels: (N,) 클러스터 레이블 (-1 = 노이즈)
        n_clusters: 유효 클러스터 수
        noise_ratio: 노이즈 비율
    """
    if logger:
        logger.info("  HDBSCAN start | shape=%s", z.shape)
    t0 = time.time()
    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size)
    labels    = clusterer.fit_predict(z)
    if logger:
        logger.info("  HDBSCAN done  | elapsed=%.1fs", time.time() - t0)

    n_clusters  = len(set(labels)) - (1 if -1 in labels else 0)
    noise_ratio = (labels == -1).sum() / len(labels)

    return labels, n_clusters, noise_ratio


# =====================
# n_components 실험
# =====================

def search_n_components(embeddings, candidates=[5, 10, 15, 20], min_cluster_size=10, logger=None):
    """
    여러 n_components 후보로 실험해서 클러스터 수, 노이즈 비율 비교
    최적 n_components 선택용
    """
    results = []
    for idx, n in enumerate(candidates, 1):
        if logger:
            logger.info("[%d/%d] n_components=%d", idx, len(candidates), n)
        z = reduce_dimensions(embeddings, n_components=n, logger=logger)
        labels, n_clusters, noise_ratio = cluster(z, min_cluster_size=min_cluster_size, logger=logger)
        results.append({
            'n_components': n,
            'n_clusters':   n_clusters,
            'noise_ratio':  noise_ratio
        })
        if logger is not None:
            logger.info(
                "n_components=%2d | clusters=%3d | noise_ratio=%.3f",
                n,
                n_clusters,
                noise_ratio,
            )

    return results


# =====================
# 시각화 (데모용 3D)
# =====================

def visualize_3d(embeddings, labels, output_path, logger=None):
    """
    3D 시각화 (데모용)
    실제 클러스터링은 10~20차원에서 수행
    """
    z3d = reduce_dimensions(embeddings, n_components=3, logger=logger)

    fig = plt.figure(figsize=(10, 8))
    ax  = fig.add_subplot(111, projection='3d')

    unique_labels = set(labels)
    colors = plt.cm.tab20(np.linspace(0, 1, len(unique_labels)))

    for label, color in zip(unique_labels, colors):
        mask = labels == label
        if label == -1:
            ax.scatter(z3d[mask, 0], z3d[mask, 1], z3d[mask, 2],
                      c='gray', s=5, alpha=0.3, label='noise')
        else:
            ax.scatter(z3d[mask, 0], z3d[mask, 1], z3d[mask, 2],
                      c=[color], s=10, alpha=0.6, label=f'cluster {label}')

    ax.set_title('User Interest Clusters (3D visualization)')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.show()
    if logger is not None:
        logger.info("Saved 3D cluster plot: %s", output_path)


# =====================
# 실행
# =====================

if __name__ == "__main__":
    OUTPUTS_DIR = Path(__file__).resolve().parent.parent / 'outputs'
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    logger, _ = setup_run_logging("cluster", OUTPUTS_DIR)

    logger.info("Outputs directory: %s", OUTPUTS_DIR)
    logger.info("Selected compute backend: cpu (NumPy/UMAP/HDBSCAN pipeline)")

    # 임베딩 로드
    embeddings = np.load(OUTPUTS_DIR / 'embeddings.npy')  # (N, 128)
    logger.info("Loaded embeddings: %s", embeddings.shape)

    # NaN 행 제거
    nan_mask = np.isnan(embeddings).any(axis=1)
    if nan_mask.any():
        logger.warning("Dropping %d NaN rows out of %d", nan_mask.sum(), len(embeddings))
        embeddings = embeddings[~nan_mask]
        logger.info("Embeddings after NaN drop: %s", embeddings.shape)

    # ── Step 1: n_components 실험 ──
    logger.info("Starting n_components search")
    results = search_n_components(
        embeddings,
        candidates=[5, 10, 15, 20],
        min_cluster_size=10,
        logger=logger,
    )

    # 클러스터 수 안정적이고 노이즈 비율 낮은 n_components 선택
    best = min(results, key=lambda x: x['noise_ratio'])
    best_n = best['n_components']
    logger.info("Selected n_components: %d", best_n)

    # ── Step 2: 실제 클러스터링 ──
    logger.info("Running final clustering")
    z      = reduce_dimensions(embeddings, n_components=best_n, logger=logger)
    labels, n_clusters, noise_ratio = cluster(z, min_cluster_size=10, logger=logger)
    logger.info("Clustering result | clusters=%d noise_ratio=%.3f", n_clusters, noise_ratio)

    # 저장
    out_path = OUTPUTS_DIR / 'cluster_labels.npy'
    np.save(out_path, labels)
    logger.info("Saved cluster labels: %s", out_path)

    # ── Step 3: 3D 시각화 (데모용) ──
    plot_path = OUTPUTS_DIR / 'clusters_3d.png'
    logger.info("Rendering 3D visualization")
    visualize_3d(embeddings, labels, plot_path, logger=logger)
