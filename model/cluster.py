import numpy as np
import umap
import hdbscan
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D


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
        labels: (N,) 클러스터 레이블 (-1 = 노이즈)
        n_clusters: 유효 클러스터 수
        noise_ratio: 노이즈 비율
    """
    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size)
    labels    = clusterer.fit_predict(z)

    n_clusters  = len(set(labels)) - (1 if -1 in labels else 0)
    noise_ratio = (labels == -1).sum() / len(labels)

    return labels, n_clusters, noise_ratio


# =====================
# n_components 실험
# =====================

def search_n_components(embeddings, candidates=[5, 10, 15, 20], min_cluster_size=10):
    """
    여러 n_components 후보로 실험해서 클러스터 수, 노이즈 비율 비교
    최적 n_components 선택용
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
        print(f"n_components={n:2d} | clusters={n_clusters:3d} | noise={noise_ratio:.3f}")

    return results


# =====================
# 시각화 (데모용 3D)
# =====================

def visualize_3d(embeddings, labels):
    """
    3D 시각화 (데모용)
    실제 클러스터링은 10~20차원에서 수행
    """
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
    plt.savefig('model/clusters_3d.png', dpi=150)
    plt.show()
    print("시각화 저장 완료: model/clusters_3d.png")


# =====================
# 실행
# =====================

if __name__ == "__main__":
    # 임베딩 로드
    embeddings = np.load('model/embeddings.npy')  # (N, 128)
    print(f"Loaded embeddings: {embeddings.shape}")

    # ── Step 1: n_components 실험 ──
    print("\n[n_components 탐색]")
    results = search_n_components(
        embeddings,
        candidates=[5, 10, 15, 20],
        min_cluster_size=10
    )

    # 클러스터 수 안정적이고 노이즈 비율 낮은 n_components 선택
    best = min(results, key=lambda x: x['noise_ratio'])
    best_n = best['n_components']
    print(f"\n최적 n_components: {best_n}")

    # ── Step 2: 실제 클러스터링 ──
    print("\n[클러스터링]")
    z      = reduce_dimensions(embeddings, n_components=best_n)
    labels, n_clusters, noise_ratio = cluster(z, min_cluster_size=10)
    print(f"클러스터 수: {n_clusters} | 노이즈 비율: {noise_ratio:.3f}")

    # 저장
    np.save('model/cluster_labels.npy', labels)
    print("클러스터 레이블 저장 완료: model/cluster_labels.npy")

    # ── Step 3: 3D 시각화 (데모용) ──
    print("\n[3D 시각화]")
    visualize_3d(embeddings, labels)
