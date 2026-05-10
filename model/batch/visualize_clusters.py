"""
유저별 클러스터 변화 시각화

사용:
    python3 -m model.batch.visualize_clusters                      # 전체 유저 생성
    python3 -m model.batch.visualize_clusters --user-id 28         # 특정 유저만
    python3 -m model.batch.visualize_clusters --user-id 28 --output my_plot.png
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
    z      = data["umap_z"][mask]
    order  = np.argsort(tps)

    # 슬라이딩 윈도우 K 데이터 (없으면 None)
    if "win_user_ids" in data:
        win_mask    = data["win_user_ids"] == user_id
        win_centers = data["win_centers"][win_mask]
        win_k       = data["win_k"][win_mask]
        win_order   = np.argsort(win_centers)
        win_centers = win_centers[win_order]
        win_k       = win_k[win_order]
    else:
        win_centers = win_k = None

    return tps[order], labels[order], z[order], win_centers, win_k


def plot_user(user_id, tps, labels, z, win_centers, win_k, out_path):
    unique_clusters = sorted(set(labels) - {-1})
    K = len(unique_clusters)
    noise_ratio = (labels == -1).mean()

    cmap = matplotlib.colormaps["tab20"].resampled(max(K, 1))
    color_map = {k: cmap(i) for i, k in enumerate(unique_clusters)}
    colors = [color_map.get(l, (0.7, 0.7, 0.7, 0.4)) for l in labels]

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

    # ── 2. 슬라이딩 윈도우 K(t) ──
    ax2 = fig.add_subplot(3, 2, (3, 4))
    if win_centers is not None and len(win_centers) > 0:
        ax2.plot(win_centers, win_k, linewidth=1.2, color="steelblue")
        ax2.fill_between(win_centers, win_k, alpha=0.2, color="steelblue")
        ax2.set_title("Sliding window K(t)  [HDBSCAN per window]")
    else:
        ax2.text(0.5, 0.5, "window data unavailable\n(T < window_size)",
                 ha="center", va="center", transform=ax2.transAxes, color="gray")
        ax2.set_title("Sliding window K(t)")
    ax2.set_xlabel("Timepoint")
    ax2.set_ylabel("Active clusters K")

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
    parser.add_argument("--user-id", type=int, default=None,
                        help="특정 유저만 생성 (생략 시 전체 유저)")
    parser.add_argument("--output",  type=str, default=None,
                        help="저장 경로 (--user-id 지정 시에만 유효)")
    args = parser.parse_args()

    OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "outputs"
    VIZ_DIR     = OUTPUTS_DIR / "viz"
    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    data_path   = OUTPUTS_DIR / "user_interests.npz"

    data       = np.load(data_path)
    all_users  = np.unique(data["labels_user_ids"])

    if args.user_id is not None:
        target_users = [args.user_id]
    else:
        target_users = all_users.tolist()
        print(f"Generating plots for {len(target_users)} users → {VIZ_DIR}")

    for uid in target_users:
        try:
            tps, labels, z, win_centers, win_k = load_user(data, uid)
        except ValueError as e:
            print(f"[skip] {e}")
            continue
        out_path = args.output if (args.user_id is not None and args.output) else str(VIZ_DIR / f"user{uid}.png")
        plot_user(uid, tps, labels, z, win_centers, win_k, out_path=out_path)
