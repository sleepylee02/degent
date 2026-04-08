"""
유저별 클러스터 변화 시각화

사용:
    python visualize_clusters.py --user-id 28
    python visualize_clusters.py --user-id 28 --window 50
    python visualize_clusters.py --user-id 28 --output my_plot.png
"""
from pathlib import Path
import argparse

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm


def load_user(data, user_id):
    mask = data["labels_user_ids"] == user_id
    if not mask.any():
        available = np.unique(data["labels_user_ids"])[:10].tolist()
        raise ValueError(f"user_id={user_id} not found. available (first 10): {available}")

    tps    = data["labels_timepoints"][mask]
    labels = data["labels"][mask]
    z      = data["umap_z"][mask]          # (T, 3)
    order  = np.argsort(tps)
    return tps[order], labels[order], z[order]


def rolling_k(tps, labels, window):
    """슬라이딩 윈도우로 구간별 유효 클러스터 수 계산"""
    T = len(tps)
    k_vals = []
    centers = []
    for i in range(T):
        lo = max(0, i - window // 2)
        hi = min(T, i + window // 2 + 1)
        k = len(set(labels[lo:hi]) - {-1})
        k_vals.append(k)
        centers.append(tps[i])
    return np.array(centers), np.array(k_vals)


def plot_user(user_id, tps, labels, z, window, out_path):
    unique_clusters = sorted(set(labels) - {-1})
    K = len(unique_clusters)
    noise_ratio = (labels == -1).mean()

    cmap = cm.get_cmap("tab20", max(K, 1))
    color_map = {k: cmap(i) for i, k in enumerate(unique_clusters)}
    colors = [color_map.get(l, (0.7, 0.7, 0.7, 0.4)) for l in labels]

    roll_x, roll_k = rolling_k(tps, labels, window)

    fig = plt.figure(figsize=(16, 12))
    fig.suptitle(f"User {user_id}  |  K={K}  |  T={len(tps)}  |  noise={noise_ratio:.1%}",
                 fontsize=13, y=0.98)

    # ── 1. 시점별 클러스터 레이블 타임라인 ──
    ax1 = fig.add_subplot(3, 2, (1, 2))
    ax1.scatter(tps, labels, c=colors, s=6, alpha=0.7)
    ax1.axhline(-1, color="gray", linewidth=0.5, linestyle="--")
    ax1.set_xlabel("Timepoint")
    ax1.set_ylabel("Cluster label")
    ax1.set_title("Cluster assignment over time")

    # ── 2. 롤링 K (슬라이딩 윈도우) ──
    ax2 = fig.add_subplot(3, 2, (3, 4))
    ax2.plot(roll_x, roll_k, linewidth=1.2, color="steelblue")
    ax2.fill_between(roll_x, roll_k, alpha=0.2, color="steelblue")
    ax2.set_xlabel("Timepoint")
    ax2.set_ylabel("Active clusters K")
    ax2.set_title(f"Rolling K  (window={window})")

    # ── 3. UMAP 3D 산점도 (처음 2축) ──
    ax3 = fig.add_subplot(3, 2, 5)
    ax3.scatter(z[:, 0], z[:, 1], c=colors, s=4, alpha=0.6)
    ax3.set_xlabel("UMAP-1")
    ax3.set_ylabel("UMAP-2")
    ax3.set_title("UMAP projection (dim 1-2)")

    # ── 4. UMAP 3D 산점도 (1·3축) ──
    ax4 = fig.add_subplot(3, 2, 6)
    ax4.scatter(z[:, 0], z[:, 2], c=colors, s=4, alpha=0.6)
    ax4.set_xlabel("UMAP-1")
    ax4.set_ylabel("UMAP-3")
    ax4.set_title("UMAP projection (dim 1-3)")

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--window",  type=int, default=30,
                        help="슬라이딩 윈도우 크기 (default: 30)")
    parser.add_argument("--output",  type=str, default=None,
                        help="저장 경로 (default: outputs/viz_user<id>.png)")
    args = parser.parse_args()

    OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"
    data_path   = OUTPUTS_DIR / "user_interests.npz"

    data = np.load(data_path)
    tps, labels, z = load_user(data, args.user_id)

    out_path = args.output or str(OUTPUTS_DIR / f"viz_user{args.user_id}.png")
    plot_user(args.user_id, tps, labels, z, window=args.window, out_path=out_path)
