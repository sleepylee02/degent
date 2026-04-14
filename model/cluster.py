from pathlib import Path
import time
import warnings

import numpy as np
import umap
import hdbscan
from tqdm import tqdm

from runtime import (
    append_metric,
    command_line,
    ensure_experiment_run,
    file_metadata,
    git_metadata,
    resolve_model_run_id,
    setup_run_logging,
    update_experiment_manifest,
)


# =====================
# 유저별 UMAP + HDBSCAN
# =====================

def cluster_user(h, min_cluster_size=10, random_state=42,
                 cluster_n_components=10, viz_n_components=3,
                 window_size=100, window_step=10):
    """
    단일 유저의 시점별 임베딩에 UMAP + HDBSCAN 수행

    h: (T, 128) — 이 유저의 시점별 hidden state (시간 순 정렬된 상태)

    UMAP은 전체 시점에 한 번만 실행 (안정적인 좌표 확보).
    HDBSCAN은 두 가지 목적으로 분리:
        global HDBSCAN : 전체 시점 대상 → 관심사 벡터 u_k 계산 (추천용)
        sliding window : 윈도우 단위 반복 → K(t) 시계열 추출 (시간 변화 추적용)

    returns:
        global_labels:    (T,)    전체 클러스터 레이블 (-1=노이즈)
        z_viz:            (T, 3)  시각화용 3D 좌표
        interest_vectors: (K, 128) 관심사 벡터 u_k
        n_clusters:       int     전체 유효 클러스터 수 K
        win_centers:      (M,)    각 윈도우의 중심 시점 인덱스
        win_k:            (M,)    각 윈도우의 유효 클러스터 수
    """
    T = len(h)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_jobs value 1 overridden", category=UserWarning)

        # Step 1a: 클러스터링용 UMAP — 10D (정보 손실 최소화)
        reducer_cluster = umap.UMAP(n_components=cluster_n_components,
                                    random_state=random_state, verbose=False)
        z_cluster = reducer_cluster.fit_transform(h)

        # Step 1b: 시각화용 UMAP — 3D (별도 실행, 클러스터링 결과에 영향 없음)
        reducer_viz = umap.UMAP(n_components=viz_n_components,
                                random_state=random_state, verbose=False)
        z_viz = reducer_viz.fit_transform(h)

    # Step 2: Global HDBSCAN — u_k 계산용
    global_labels = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(z_cluster)
    unique_clusters = sorted(set(global_labels) - {-1})
    if unique_clusters:
        interest_vectors = np.stack([h[global_labels == k].mean(axis=0) for k in unique_clusters])
    else:
        interest_vectors = np.zeros((0, h.shape[1]))

    # Step 3: Sliding window HDBSCAN — K(t) 시계열
    win_centers, win_k = [], []
    if T >= window_size:
        for start in range(0, T - window_size + 1, window_step):
            end     = start + window_size
            z_win   = z_cluster[start:end]
            labels_win = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(z_win)
            win_centers.append((start + end) // 2)
            win_k.append(len(set(labels_win) - {-1}))

    return (global_labels, z_viz, interest_vectors, len(unique_clusters),
            np.array(win_centers), np.array(win_k))


def run_per_user_clustering(embeddings, user_ids, timepoint_idx,
                            min_cluster_size=10, stride=1,
                            window_size=100, window_step=10,
                            random_state=42, cluster_n_components=10,
                            viz_n_components=3, logger=None):
    """
    모든 유저에 대해 개별 UMAP + HDBSCAN 수행

    stride:      시간 순 정렬 후 매 N번째 시점만 사용 (기본 1 = 전체)
    window_size: 슬라이딩 윈도우 크기 (기본 100)
    window_step: 윈도우 이동 간격 (기본 10)

    returns: {user_id: {
        'timepoints':    (T,)    시점 index (시간 순)
        'labels':        (T,)    전체 클러스터 레이블
        'z':             (T, 3)  시각화용 UMAP 좌표
        'interest_vectors': (K, 128) 관심사 벡터 u_k
        'n_clusters':    int     전체 유효 K
        'win_centers':   (M,)    윈도우 중심 timepoint
        'win_k':         (M,)    윈도우별 K
    }}
    """
    unique_users = np.unique(user_ids)
    results  = {}
    skipped  = 0

    for uid in tqdm(unique_users, desc="per-user clustering"):
        mask = user_ids == uid
        h    = embeddings[mask]
        tp   = timepoint_idx[mask]

        # 시간 순 정렬
        order = np.argsort(tp)
        h  = h[order]
        tp = tp[order]

        # stride 적용 — 매 N번째 시점만 사용
        if stride > 1:
            h  = h[::stride]
            tp = tp[::stride]

        # 최소 포인트 수 미달 시 스킵 (UMAP 최소 요건)
        if len(h) < min_cluster_size * 2:
            skipped += 1
            continue

        global_labels, z, interest_vectors, n_clusters, win_centers, win_k = cluster_user(
            h, min_cluster_size=min_cluster_size,
            random_state=random_state,
            cluster_n_components=cluster_n_components,
            viz_n_components=viz_n_components,
            window_size=window_size, window_step=window_step,
        )

        # win_centers는 h 내 인덱스 → 실제 timepoint로 변환
        win_tp_centers = tp[win_centers] if len(win_centers) > 0 else np.array([], dtype=int)

        results[uid] = {
            'timepoints':       tp,
            'labels':           global_labels,
            'z':                z,
            'interest_vectors': interest_vectors,
            'n_clusters':       n_clusters,
            'win_centers':      win_tp_centers,
            'win_k':            win_k,
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
    win_users, win_centers_all, win_k_all  = [], [], []
    uid_list, n_clusters_list              = [], []

    for uid, res in results.items():
        T = len(res['timepoints'])
        lbl_users.extend([uid] * T)
        lbl_tps.extend(res['timepoints'].tolist())
        lbl_labels.extend(res['labels'].tolist())
        lbl_z.append(res['z'])

        for k, vec in enumerate(res['interest_vectors']):
            iv_users.append(uid)
            iv_clusters.append(k)
            ivs.append(vec)

        M = len(res['win_centers'])
        win_users.extend([uid] * M)
        win_centers_all.extend(res['win_centers'].tolist())
        win_k_all.extend(res['win_k'].tolist())

        uid_list.append(uid)
        n_clusters_list.append(res['n_clusters'])

    d = ivs[0].shape[0] if ivs else 128
    np.savez(
        output_path,
        labels_user_ids   = np.array(lbl_users),
        labels_timepoints = np.array(lbl_tps),
        labels            = np.array(lbl_labels),
        umap_z            = np.concatenate(lbl_z, axis=0) if lbl_z else np.zeros((0, 3)),
        iv_user_ids       = np.array(iv_users),
        iv_cluster_ids    = np.array(iv_clusters),
        interest_vectors  = np.array(ivs) if ivs else np.zeros((0, d)),
        win_user_ids      = np.array(win_users),
        win_centers       = np.array(win_centers_all),
        win_k             = np.array(win_k_all),
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
    parser.add_argument("--top-n",       type=int, default=None,
                        help="시퀀스가 긴 상위 N명만 실행 (기본: 전체)")
    parser.add_argument("--stride",      type=int, default=1,
                        help="시점 샘플링 간격 (기본: 1 = 전체, 2이면 절반)")
    parser.add_argument("--window-size", type=int, default=100,
                        help="슬라이딩 윈도우 크기 (기본: 100)")
    parser.add_argument("--window-step", type=int, default=10,
                        help="윈도우 이동 간격 (기본: 10)")
    parser.add_argument("--min-cluster-size", type=int, default=10,
                        help="HDBSCAN 최소 클러스터 크기 (기본: 10)")
    parser.add_argument("--cluster-dim", type=int, default=10,
                        help="클러스터링용 UMAP 차원 (기본: 10)")
    parser.add_argument("--viz-dim", type=int, default=3,
                        help="시각화용 UMAP 차원 (기본: 3)")
    parser.add_argument("--random-state", type=int, default=42,
                        help="UMAP random_state (기본: 42)")
    parser.add_argument("--run-id", type=str, default=None,
                        help="Experiment run id. Defaults to latest model run.")
    parser.add_argument("--hash-inputs", action="store_true",
                        help="Compute SHA256 for input artifact files.")
    parser.add_argument("--hash-limit-mb", type=int, default=100,
                        help="Max file size for SHA256 hashing. Use -1 for no limit.")
    args = parser.parse_args()

    ROOT = Path(__file__).resolve().parent.parent
    OUTPUTS_DIR = ROOT / 'outputs'
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = resolve_model_run_id(OUTPUTS_DIR, args.run_id, prefer_latest=True)
    run_dir = ensure_experiment_run(ROOT, run_id)
    logger, log_path = setup_run_logging("cluster", OUTPUTS_DIR)
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024
    embeddings_path = OUTPUTS_DIR / 'embeddings.npz'

    logger.info("Experiment run id: %s", run_id)
    logger.info("Experiment metadata directory: %s", run_dir)
    logger.info("Outputs directory: %s", OUTPUTS_DIR)

    update_experiment_manifest(
        run_dir,
        {
            "run_id": run_id,
            "git": git_metadata(ROOT),
            "stages": {
                "cluster": {
                    "command": command_line(),
                    "log": file_metadata(log_path, root=ROOT),
                    "inputs": {
                        "embeddings": file_metadata(
                            embeddings_path,
                            root=ROOT,
                            include_sha256=args.hash_inputs,
                            sha256_limit_bytes=hash_limit_bytes,
                        ),
                    },
                    "clustering_config": {
                        "user_id": args.user_id,
                        "top_n": args.top_n,
                        "stride": args.stride,
                        "window_size": args.window_size,
                        "window_step": args.window_step,
                        "min_cluster_size": args.min_cluster_size,
                        "cluster_n_components": args.cluster_dim,
                        "viz_n_components": args.viz_dim,
                        "random_state": args.random_state,
                    },
                }
            },
        },
    )

    # embeddings.npz 로드 (extract.py 출력)
    data          = np.load(embeddings_path)
    embeddings    = data['embeddings']     # (N, 128)
    user_ids      = data['user_ids']       # (N,)
    timepoint_idx = data['timepoint_idx']  # (N,)
    initial_embedding_rows = int(embeddings.shape[0])
    initial_unique_users = int(len(np.unique(user_ids)))
    logger.info("Loaded embeddings: %s | unique users: %d",
                embeddings.shape, initial_unique_users)

    # NaN 행 제거
    nan_mask = np.isnan(embeddings).any(axis=1)
    dropped_nan_rows = int(nan_mask.sum())
    if nan_mask.any():
        logger.warning("Dropping %d NaN rows", dropped_nan_rows)
        embeddings    = embeddings[~nan_mask]
        user_ids      = user_ids[~nan_mask]
        timepoint_idx = timepoint_idx[~nan_mask]

    # top-n 모드: 시퀀스가 긴 상위 N명만 필터링
    if args.top_n is not None:
        counts = {uid: (user_ids == uid).sum() for uid in np.unique(user_ids)}
        top_users = set(sorted(counts, key=counts.get, reverse=True)[:args.top_n])
        mask = np.isin(user_ids, list(top_users))
        embeddings    = embeddings[mask]
        user_ids      = user_ids[mask]
        timepoint_idx = timepoint_idx[mask]
        logger.info("Top-%d users | min_timepoints=%d max_timepoints=%d",
                    args.top_n,
                    min(counts[u] for u in top_users),
                    max(counts[u] for u in top_users))

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
    logger.info(
        "Starting per-user clustering | cluster_dim=%d viz_dim=%d "
        "min_cluster_size=%d stride=%d window_size=%d window_step=%d random_state=%d",
        args.cluster_dim,
        args.viz_dim,
        args.min_cluster_size,
        args.stride,
        args.window_size,
        args.window_step,
        args.random_state,
    )
    t0      = time.time()
    results = run_per_user_clustering(
        embeddings, user_ids, timepoint_idx,
        min_cluster_size=args.min_cluster_size,
        stride=args.stride,
        window_size=args.window_size,
        window_step=args.window_step,
        random_state=args.random_state,
        cluster_n_components=args.cluster_dim,
        viz_n_components=args.viz_dim,
        logger=logger,
    )
    elapsed_seconds = time.time() - t0
    logger.info("Total elapsed: %.1fs", elapsed_seconds)

    # K 분포 요약
    k_values = [r['n_clusters'] for r in results.values()]
    if k_values:
        k_summary = {
            "min": int(min(k_values)),
            "max": int(max(k_values)),
            "mean": float(np.mean(k_values)),
            "median": float(np.median(k_values)),
        }
        logger.info("K distribution | min=%d  max=%d  mean=%.1f  median=%.1f",
                    k_summary["min"], k_summary["max"],
                    k_summary["mean"], k_summary["median"])
    else:
        k_summary = {"min": None, "max": None, "mean": None, "median": None}
        logger.warning("K distribution unavailable: no users clustered")

    # 저장
    out_path = OUTPUTS_DIR / 'user_interests.npz'
    save_results(results, out_path, logger=logger)

    metric_record = {
        "stage": "cluster",
        "input_embedding_rows": initial_embedding_rows,
        "input_unique_users": initial_unique_users,
        "dropped_nan_rows": dropped_nan_rows,
        "clustered_users": int(len(results)),
        "filtered_embedding_rows": int(embeddings.shape[0]),
        "filtered_unique_users": int(len(np.unique(user_ids))),
        "elapsed_seconds": float(elapsed_seconds),
        "k_min": k_summary["min"],
        "k_max": k_summary["max"],
        "k_mean": k_summary["mean"],
        "k_median": k_summary["median"],
    }
    append_metric(run_dir, metric_record)
    update_experiment_manifest(
        run_dir,
        {
            "stages": {
                "cluster": {
                    "data_summary": {
                        "initial_embedding_rows": initial_embedding_rows,
                        "initial_unique_users": initial_unique_users,
                        "dropped_nan_rows": dropped_nan_rows,
                        "filtered_embedding_rows": int(embeddings.shape[0]),
                        "filtered_unique_users": int(len(np.unique(user_ids))),
                    },
                    "outputs": {
                        "user_interests": file_metadata(
                            out_path,
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
