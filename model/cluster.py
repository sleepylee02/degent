from pathlib import Path
 
import numpy as np
import umap
import hdbscan
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
 
from runtime import setup_run_logging
 
 
# =====================
# UMAP 차원 축소
# =====================
 
def reduce_dimensions(embeddings, n_components, random_state=42):
    """
    embeddings: (N, d_model) numpy array
    n_components: 축소할 차원 수
    returns: (N, n_components) numpy array
    """
    reducer = umap.UMAP(n_components=n_components, random_state=random_state)
    return reducer.fit_transform(embeddings)
 
 
# =====================
# HDBSCAN 클러스터링
# =====================
 
def cluster(z, min_cluster_size=10):
    """
    z: (N, n_components) numpy array
    returns:
        labels:      (N,) 클러스터 레이블 (-1 = 노이즈)
        n_clusters:  유효 클러스터 수
        noise_ratio: 노이즈 비율
    """
    clusterer  = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size)
    labels     = clusterer.fit_predict(z)
    n_clusters  = len(set(labels)) - (1 if -1 in labels else 0)
    noise_ratio = (labels == -1).sum() / len(labels)
    return labels, n_clusters, noise_ratio
 
 
# =====================
# n_components 실험
# =====================
 
def search_n_components(embeddings, candidates=[5, 10, 15, 20], min_cluster_size=10, logger=None):
    """
    여러 n_components 후보로 실험해서 클러스터 수, 노이즈 비율 비교
    """
    results = []
    for n in candidates:
        z = reduce_dimensions(embeddings, n_components=n)
        labels, n_clusters, noise_ratio = cluster(z, min_cluster_size=min_cluster_size)
        results.append({
            'n_components': n,
            'n_clusters':   n_clusters,
            'noise_ratio':  noise_ratio
        })
        if logger:
            logger.info(
                "n_components=%2d | clusters=%3d | noise_ratio=%.3f",
                n, n_clusters, noise_ratio,
            )
    return results
 
 
# =====================
# 시각화 (데모용 3D)
# =====================
 
def visualize_3d(embeddings, labels, output_path, logger=None):
    """3D 시각화 (데모용) - 실제 클러스터링은 10~20차원에서 수행"""
    z3d = reduce_dimensions(embeddings, n_components=3)
 
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
    if logger:
        logger.info("Saved 3D cluster plot: %s", output_path)
 
 
# =====================
# 유저별, 시점별 분석
# =====================
 
def analyze_user_clusters(labels, user_ids, timepoint_idx, logger=None):
    """
    유저별로 시점에 따른 클러스터 변화 분석
 
    예시 출력:
        user_id=1 | timepoint=0 → cluster=2
        user_id=1 | timepoint=1 → cluster=5  ← 관심사 변화
    """
    unique_users = np.unique(user_ids)
    user_cluster_map = {}
 
    for uid in unique_users:
        mask      = user_ids == uid
        tp_labels = labels[mask]
        tp_idx    = timepoint_idx[mask]
 
        # 시점 순서대로 정렬
        order     = np.argsort(tp_idx)
        tp_labels = tp_labels[order]
        tp_idx    = tp_idx[order]
 
        user_cluster_map[uid] = list(zip(tp_idx.tolist(), tp_labels.tolist()))
 
    if logger:
        # 예시로 상위 3명 출력
        for uid in list(unique_users[:3]):
            for tp, cl in user_cluster_map[uid]:
                logger.info("user_id=%d | timepoint=%d → cluster=%d", uid, tp, cl)
 
    return user_cluster_map  # {user_id: [(timepoint, cluster_label), ...]}
 
 
# =====================
# 실행
# =====================
 
if __name__ == "__main__":
    OUTPUTS_DIR = Path(__file__).resolve().parent.parent / 'outputs'
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    logger, _ = setup_run_logging("cluster", OUTPUTS_DIR)
 
    logger.info("Outputs directory: %s", OUTPUTS_DIR)
 
    # npz 로드 (유저별, 시점별 정보 포함)
    data          = np.load(OUTPUTS_DIR / 'embeddings.npz')
    embeddings    = data['embeddings']     # (N, 128)
    user_ids      = data['user_ids']       # (N,)
    timepoint_idx = data['timepoint_idx']  # (N,)
    logger.info("Loaded embeddings: %s | unique users: %d", embeddings.shape, len(np.unique(user_ids)))
 
    # ── Step 1: n_components 실험 ──
    logger.info("Starting n_components search")
    results = search_n_components(
        embeddings,
        candidates=[5, 10, 15, 20],
        min_cluster_size=10,
        logger=logger,
    )
 
    best   = min(results, key=lambda x: x['noise_ratio'])
    best_n = best['n_components']
    logger.info("Selected n_components: %d", best_n)
 
    # ── Step 2: 실제 클러스터링 ──
    logger.info("Running final clustering with n_components=%d", best_n)
    z      = reduce_dimensions(embeddings, n_components=best_n)
    labels, n_clusters, noise_ratio = cluster(z, min_cluster_size=10)
    logger.info("Clustering result | clusters=%d noise_ratio=%.3f", n_clusters, noise_ratio)
 
    # 저장 (유저별, 시점별 정보 포함)
    out_path = OUTPUTS_DIR / 'cluster_results.npz'
    np.savez(
        out_path,
        labels        = labels,        # (N,)
        user_ids      = user_ids,      # (N,)
        timepoint_idx = timepoint_idx  # (N,)
    )
    logger.info("Saved cluster results: %s", out_path)
 
    # ── Step 3: 유저별, 시점별 클러스터 분석 ──
    logger.info("Analyzing user-level cluster transitions")
    user_cluster_map = analyze_user_clusters(labels, user_ids, timepoint_idx, logger=logger)
    logger.info("User cluster map built | total users=%d", len(user_cluster_map))
 
    # ── Step 4: 3D 시각화 (데모용) ──
    plot_path = OUTPUTS_DIR / 'clusters_3d.png'
    logger.info("Rendering 3D visualization")
    visualize_3d(embeddings, labels, plot_path, logger=logger)