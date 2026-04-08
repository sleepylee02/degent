from pathlib import Path
import time

import numpy as np
import umap
import hdbscan
from tqdm import tqdm

from runtime import setup_run_logging


# =====================
# 유저별 UMAP + HDBSCAN
# =====================

def cluster_user(h, min_cluster_size=10, random_state=42):
    """
    단일 유저의 시점별 임베딩에 UMAP + HDBSCAN 수행

    h: (T, 128) — 이 유저의 시점별 hidden state (시간 순 정렬된 상태)
    returns:
        labels:           (T,) 클러스터 레이블 (-1 = 노이즈)
        interest_vectors: (K, 128) 클러스터별 관심사 벡터 u_k (원본 128d 평균)
        n_clusters:       유효 클러스터 수 K
    """
    # Step 2: UMAP h_t ∈ R^128 → z_t ∈ R^3
    reducer = umap.UMAP(n_components=3, random_state=random_state, verbose=False)
    z = reducer.fit_transform(h)

    # Step 3: HDBSCAN → adaptive K
    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size)
    labels    = clusterer.fit_predict(z)

    # Step 4: u_k = mean(h_t for t ∈ C_k) — 원본 128d 공간에서 계산
    unique_clusters = sorted(set(labels) - {-1})
    if unique_clusters:
        interest_vectors = np.stack([h[labels == k].mean(axis=0) for k in unique_clusters])
    else:
        interest_vectors = np.zeros((0, h.shape[1]))

    return labels, z, interest_vectors, len(unique_clusters)


def run_per_user_clustering(embeddings, user_ids, timepoint_idx,
                            min_cluster_size=10, logger=None):
    """
    모든 유저에 대해 개별 UMAP + HDBSCAN 수행

    returns: {user_id: {
        'timepoints':       (T,)    시점 index (시간 순)
        'labels':           (T,)    시점별 클러스터 레이블
        'interest_vectors': (K, 128) 관심사 벡터 u_k
        'n_clusters':       int     adaptive K
    }}
    """
    unique_users = np.unique(user_ids)
    results  = {}
    skipped  = 0

    for uid in tqdm(unique_users, desc="per-user clustering"):
        mask = user_ids == uid
        h    = embeddings[mask]       # (T, 128)
        tp   = timepoint_idx[mask]    # (T,)

        # 시간 순 정렬
        order = np.argsort(tp)
        h  = h[order]
        tp = tp[order]

        # 최소 포인트 수 미달 시 스킵 (UMAP 최소 요건)
        if len(h) < min_cluster_size * 2:
            skipped += 1
            continue

        labels, z, interest_vectors, n_clusters = cluster_user(
            h, min_cluster_size=min_cluster_size
        )

        results[uid] = {
            'timepoints':       tp,
            'labels':           labels,
            'z':                z,
            'interest_vectors': interest_vectors,
            'n_clusters':       n_clusters,
        }

    if logger:
        logger.info("Clustering done | users=%d skipped=%d", len(results), skipped)

    return results


# =====================
# 저장
# =====================

def save_results(results, output_path, logger=None):
    """
    per-user 결과를 flat arrays로 저장

    저장 키:
        labels_user_ids:    (N,)      각 시점의 user_id
        labels_timepoints:  (N,)      각 시점의 timepoint index
        labels:             (N,)      각 시점의 클러스터 레이블 (-1=노이즈)
        iv_user_ids:        (M,)      각 관심사 벡터의 user_id
        iv_cluster_ids:     (M,)      각 관심사 벡터의 클러스터 id
        interest_vectors:   (M, 128)  관심사 벡터 u_k
        user_ids_list:      (U,)      유저 id 목록
        n_clusters:         (U,)      유저별 클러스터 수 K
    """
    lbl_users, lbl_tps, lbl_labels, lbl_z = [], [], [], []
    iv_users, iv_clusters, ivs             = [], [], []
    uid_list, n_clusters_list              = [], []

    for uid, res in results.items():
        T = len(res['timepoints'])
        lbl_users.extend([uid] * T)
        lbl_tps.extend(res['timepoints'].tolist())
        lbl_labels.extend(res['labels'].tolist())
        lbl_z.append(res['z'])  # (T, 3)

        for k, vec in enumerate(res['interest_vectors']):
            iv_users.append(uid)
            iv_clusters.append(k)
            ivs.append(vec)

        uid_list.append(uid)
        n_clusters_list.append(res['n_clusters'])

    d = ivs[0].shape[0] if ivs else 128  # d_model
    np.savez(
        output_path,
        labels_user_ids   = np.array(lbl_users),
        labels_timepoints = np.array(lbl_tps),
        labels            = np.array(lbl_labels),
        umap_z            = np.concatenate(lbl_z, axis=0) if lbl_z else np.zeros((0, 3)),
        iv_user_ids       = np.array(iv_users),
        iv_cluster_ids    = np.array(iv_clusters),
        interest_vectors  = np.array(ivs) if ivs else np.zeros((0, d)),
        user_ids_list     = np.array(uid_list),
        n_clusters        = np.array(n_clusters_list),
    )
    if logger:
        logger.info("Saved: %s", output_path)


# =====================
# 실행
# =====================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=None,
                        help="테스트용: 특정 유저 한 명만 실행")
    args = parser.parse_args()

    OUTPUTS_DIR = Path(__file__).resolve().parent.parent / 'outputs'
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    logger, _ = setup_run_logging("cluster", OUTPUTS_DIR)

    logger.info("Outputs directory: %s", OUTPUTS_DIR)

    # embeddings.npz 로드 (extract.py 출력)
    data          = np.load(OUTPUTS_DIR / 'embeddings.npz')
    embeddings    = data['embeddings']     # (N, 128)
    user_ids      = data['user_ids']       # (N,)
    timepoint_idx = data['timepoint_idx']  # (N,)
    logger.info("Loaded embeddings: %s | unique users: %d",
                embeddings.shape, len(np.unique(user_ids)))

    # NaN 행 제거
    nan_mask = np.isnan(embeddings).any(axis=1)
    if nan_mask.any():
        logger.warning("Dropping %d NaN rows", nan_mask.sum())
        embeddings    = embeddings[~nan_mask]
        user_ids      = user_ids[~nan_mask]
        timepoint_idx = timepoint_idx[~nan_mask]

    # 테스트 모드: 특정 유저만 필터링
    if args.user_id is not None:
        mask = user_ids == args.user_id
        if not mask.any():
            available = np.unique(user_ids)[:10].tolist()
            logger.error("user_id=%d not found. available (first 10): %s",
                         args.user_id, available)
            raise SystemExit(1)
        embeddings    = embeddings[mask]
        user_ids      = user_ids[mask]
        timepoint_idx = timepoint_idx[mask]
        logger.info("Test mode: user_id=%d | timepoints=%d", args.user_id, mask.sum())

    # 유저별 UMAP(R^3) + HDBSCAN
    logger.info("Starting per-user clustering (n_components=3, min_cluster_size=10)")
    t0      = time.time()
    results = run_per_user_clustering(
        embeddings, user_ids, timepoint_idx,
        min_cluster_size=10,
        logger=logger,
    )
    logger.info("Total elapsed: %.1fs", time.time() - t0)

    # K 분포 요약
    k_values = [r['n_clusters'] for r in results.values()]
    logger.info("K distribution | min=%d  max=%d  mean=%.1f  median=%.1f",
                min(k_values), max(k_values),
                np.mean(k_values), np.median(k_values))

    # 저장
    out_path = OUTPUTS_DIR / 'user_interests.npz'
    save_results(results, out_path, logger=logger)
