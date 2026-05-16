from pathlib import Path
import time
import warnings

import numpy as np
import umap
import hdbscan
from tqdm import tqdm

import model.stream.runtime_store as runtime_store
from model.common.cluster import (
    choose_backend,
    cluster_embeddings,
    cluster_with_fallback,
    compute_interest_vectors,
    top_genres_for_cluster,
)
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
    save_interest_state,
)

INTEREST_STATE_VERSION = "batch_interest_state.v1"


# =====================
# 유저별 클러스터링
# =====================

def cluster_user(h, *, backend, min_cluster_size=10, random_state=42,
                 cluster_n_components=10, viz_n_components=3,
                 window_size=100, window_step=10, logger=None):
    """
    단일 유저의 시점별 임베딩에 UMAP + HDBSCAN 수행.

    h: (T, 128) — 시간 순 정렬된 hidden state

    클러스터링용 UMAP+HDBSCAN: common.cluster (GPU/CPU 자동)
    시각화용 UMAP: CPU only (별도 실행, 클러스터링 결과에 영향 없음)
    슬라이딩 윈도우 HDBSCAN: K(t) 시계열 추출용 (CPU)

    returns:
        global_labels:    (T,)     전체 클러스터 레이블 (-1=노이즈)
        z_viz:            (T, 3)   시각화용 3D 좌표
        interest_vectors: (K, 128) 관심사 벡터 u_k
        n_clusters:       int
        win_centers:      (M,)
        win_k:            (M,)
    """
    T = len(h)

    # Step 1: 클러스터링 (GPU/CPU)
    result = cluster_embeddings(
        h,
        backend=backend,
        min_cluster_size=min_cluster_size,
        cluster_dim=cluster_n_components,
        random_state=random_state,
    )
    z_cluster = result.z_cluster
    global_labels = result.labels

    # Step 2: 시각화용 UMAP (CPU, 클러스터링과 독립)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_jobs value 1 overridden", category=UserWarning)
        reducer_viz = umap.UMAP(n_components=viz_n_components,
                                random_state=random_state, verbose=False)
        z_viz = reducer_viz.fit_transform(h)

    # Step 3: Interest vectors — 원본 128D 공간에서 centroid
    interest_vectors = compute_interest_vectors(h, global_labels)

    # Step 4: Sliding window HDBSCAN — K(t) 시계열
    win_centers, win_k = [], []
    if T >= window_size:
        for start in range(0, T - window_size + 1, window_step):
            end = start + window_size
            z_win = z_cluster[start:end]
            labels_win = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(z_win)
            win_centers.append((start + end) // 2)
            win_k.append(len(set(labels_win) - {-1}))

    return (global_labels, z_viz, interest_vectors, len(interest_vectors),
            np.array(win_centers), np.array(win_k))


def run_per_user_clustering(embeddings, user_ids, timepoint_idx,
                            movie_ids_array=None,
                            backend="cpu",
                            min_cluster_size=10, stride=1,
                            window_size=100, window_step=10,
                            random_state=42, cluster_n_components=10,
                            viz_n_components=3, logger=None):
    unique_users = np.unique(user_ids)
    results = {}
    skipped = 0

    for uid in tqdm(unique_users, desc="per-user clustering"):
        mask = user_ids == uid
        h = embeddings[mask]
        tp = timepoint_idx[mask]
        uid_movie_ids = movie_ids_array[mask] if movie_ids_array is not None else None

        order = np.argsort(tp)
        h = h[order]
        tp = tp[order]
        if uid_movie_ids is not None:
            uid_movie_ids = uid_movie_ids[order]

        if stride > 1:
            h = h[::stride]
            tp = tp[::stride]
            if uid_movie_ids is not None:
                uid_movie_ids = uid_movie_ids[::stride]

        if len(h) < min_cluster_size * 2:
            skipped += 1
            continue

        global_labels, z, interest_vectors, n_clusters, win_centers, win_k = cluster_user(
            h,
            backend=backend,
            min_cluster_size=min_cluster_size,
            random_state=random_state,
            cluster_n_components=cluster_n_components,
            viz_n_components=viz_n_components,
            window_size=window_size,
            window_step=window_step,
            logger=logger,
        )

        win_tp_centers = tp[win_centers] if len(win_centers) > 0 else np.array([], dtype=int)

        results[uid] = {
            "timepoints":       tp,
            "labels":           global_labels,
            "z":                z,
            "interest_vectors": interest_vectors,
            "n_clusters":       n_clusters,
            "win_centers":      win_tp_centers,
            "win_k":            win_k,
            "movie_ids":        uid_movie_ids,
        }

    if logger:
        logger.info("Clustering done | users=%d skipped=%d", len(results), skipped)

    return results


# =====================
# 저장
# =====================

def save_interest_states(results, state_dir=None, *, state_db=None, run_id=None, backend,
                         genre_map_idx=None, all_genres=None, logger=None):
    """
    유저별 interest vectors를 SQLite state store와 optional legacy JSON dir에 저장.
    stream/interest_assign.py의 InterestState 포맷과 호환.
    """
    if state_dir is None and state_db is None:
        raise ValueError("Either state_db or state_dir must be provided for interest state output.")
    state_dir = None if state_dir is None else Path(state_dir)
    state_db = None if state_db is None else Path(state_db)
    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True)
    if state_db is not None:
        if run_id is None:
            raise ValueError("run_id is required when writing interest states to state_db.")
        runtime_store.init_store(state_db)
    timestamp = local_timestamp()
    saved = 0
    has_genre_info = genre_map_idx is not None and all_genres is not None

    for uid, res in results.items():
        uid_movie_ids = res.get("movie_ids")
        interests = []
        for k, vec in enumerate(res["interest_vectors"]):
            mask = res["labels"] == k
            genres = []
            if has_genre_info and uid_movie_ids is not None:
                genres = top_genres_for_cluster(uid_movie_ids[mask], genre_map_idx, all_genres)
            interests.append(Interest(
                interest_id=k,
                vector=vec.astype(float).tolist(),
                assigned_count=int(mask.sum()),
                created_at=timestamp,
                updated_at=timestamp,
                source=f"batch_cluster:{backend}:cluster_{k}",
                top_genres=genres,
            ))

        state = InterestState(
            user_id=int(uid),
            embedding_dim=int(res["interest_vectors"].shape[1]) if len(res["interest_vectors"]) else 128,
            interests=interests,
            pending_raw_event_ids=[],
            processed_raw_event_ids=[],
            version=INTEREST_STATE_VERSION,
        )
        if state_dir is not None:
            path = state_dir / f"{int(uid)}.json"
            save_interest_state(state, path)
        if state_db is not None:
            runtime_store.record_interest_state(state_db, run_id=run_id, state=state)
        saved += 1

    if logger:
        logger.info("Saved interest states | users=%d state_db=%s legacy_dir=%s", saved, state_db, state_dir)


def save_viz_npz(results, output_path, logger=None):
    """
    시각화용 데이터(umap_z, labels, timepoints, sliding window)를 npz로 저장.
    visualize_clusters.py가 읽는 포맷을 유지한다.
    interest_vectors는 interest_states JSON으로 분리됐으므로 여기서는 제외.
    """
    lbl_users, lbl_tps, lbl_labels, lbl_z = [], [], [], []
    win_users, win_centers_all, win_k_all = [], [], []

    for uid, res in results.items():
        T = len(res["timepoints"])
        lbl_users.extend([uid] * T)
        lbl_tps.extend(res["timepoints"].tolist())
        lbl_labels.extend(res["labels"].tolist())
        lbl_z.append(res["z"])

        M = len(res["win_centers"])
        win_users.extend([uid] * M)
        win_centers_all.extend(res["win_centers"].tolist())
        win_k_all.extend(res["win_k"].tolist())

    np.savez(
        output_path,
        labels_user_ids   = np.array(lbl_users),
        labels_timepoints = np.array(lbl_tps),
        labels            = np.array(lbl_labels),
        umap_z            = np.concatenate(lbl_z, axis=0) if lbl_z else np.zeros((0, 3)),
        win_user_ids      = np.array(win_users),
        win_centers       = np.array(win_centers_all),
        win_k             = np.array(win_k_all),
    )
    if logger:
        logger.info("Saved viz npz: %s", output_path)


# =====================
# 실행
# =====================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id",          type=int,   default=None)
    parser.add_argument("--top-n",            type=int,   default=None)
    parser.add_argument("--stride",           type=int,   default=1)
    parser.add_argument("--window-size",      type=int,   default=100)
    parser.add_argument("--window-step",      type=int,   default=10)
    parser.add_argument("--min-cluster-size", type=int,   default=10)
    parser.add_argument("--cluster-dim",      type=int,   default=10)
    parser.add_argument("--viz-dim",          type=int,   default=3)
    parser.add_argument("--random-state",     type=int,   default=42)
    parser.add_argument("--cluster-backend",  type=str,   default="auto",
                        choices=["auto", "gpu", "cpu"])
    parser.add_argument("--state-db", type=Path, default=Path("outputs/batch/state.sqlite"),
                        help="SQLite state store for batch interest states.")
    parser.add_argument("--reset-state-db", action="store_true",
                        help="Remove state-db before writing batch interest states.")
    parser.add_argument("--interest-state-dir", type=Path, default=None,
                        help="Optional legacy per-user JSON interest state output directory.")
    parser.add_argument("--run-id",           type=str,   default=None)
    parser.add_argument("--hash-inputs",      action="store_true")
    parser.add_argument("--hash-limit-mb",    type=int,   default=100)
    parser.add_argument("--movies",           type=Path,  default=Path("data/movies_processed_drop.csv"),
                        help="Movies metadata CSV for genre labeling.")
    parser.add_argument("--embeddings",       type=Path,  default=Path("outputs/canonical_embeddings.npz"),
                        help="Input embeddings npz (canonical_embeddings.npz or embeddings.npz).")
    parser.add_argument("--output",           type=Path,  default=Path("outputs/user_interests.npz"),
                        help="Output NPZ for dashboard/export visualization data.")
    args = parser.parse_args()

    ROOT        = Path(__file__).resolve().parents[2]
    OUTPUTS_DIR = ROOT / "outputs"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id  = resolve_model_run_id(OUTPUTS_DIR, args.run_id, prefer_latest=True)
    run_dir = ensure_experiment_run(ROOT, run_id)
    logger, log_path = setup_run_logging("cluster", OUTPUTS_DIR)
    hash_limit_bytes = None if args.hash_limit_mb < 0 else args.hash_limit_mb * 1024 * 1024
    embeddings_path  = args.embeddings if args.embeddings.is_absolute() else ROOT / args.embeddings
    interest_state_dir = None if args.interest_state_dir is None else (
        args.interest_state_dir if args.interest_state_dir.is_absolute() else ROOT / args.interest_state_dir
    )
    state_db_path = args.state_db if args.state_db.is_absolute() else ROOT / args.state_db
    if args.reset_state_db and state_db_path.exists():
        state_db_path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = state_db_path.with_name(state_db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
    viz_npz_path = args.output if args.output.is_absolute() else ROOT / args.output
    viz_npz_path.parent.mkdir(parents=True, exist_ok=True)

    genre_map_idx = None
    all_genres = None
    movies_path = args.movies if args.movies.is_absolute() else ROOT / args.movies
    if movies_path.exists():
        import pandas as pd
        from model.common.dataset import build_genre_map
        movies_df = pd.read_csv(movies_path)
        genre_map_idx, all_genres = build_genre_map(movies_df)
        logger.info("Loaded genre map: %d movies %d genres", len(genre_map_idx), len(all_genres))
    else:
        logger.warning("Movies file not found, genre labeling disabled: %s", movies_path)

    selected_backend, fallback_reason = choose_backend(args.cluster_backend)
    logger.info("Experiment run id: %s", run_id)
    logger.info("Outputs directory: %s", OUTPUTS_DIR)
    logger.info("Interest state DB: %s", state_db_path)
    logger.info("Legacy interest state dir: %s", interest_state_dir)
    logger.info("Cluster backend requested=%s selected=%s fallback_reason=%s",
                args.cluster_backend, selected_backend, fallback_reason)

    update_experiment_manifest(run_dir, {
        "run_id": run_id,
        "git": git_metadata(ROOT),
        "stages": {
            "cluster": {
                "command": command_line(),
                "log": file_metadata(log_path, root=ROOT),
                "inputs": {
                    "embeddings": file_metadata(
                        embeddings_path, root=ROOT,
                        include_sha256=args.hash_inputs,
                        sha256_limit_bytes=hash_limit_bytes,
                    ),
                },
                "clustering_config": {
                    "user_id":            args.user_id,
                    "top_n":              args.top_n,
                    "stride":             args.stride,
                    "window_size":        args.window_size,
                    "window_step":        args.window_step,
                    "min_cluster_size":   args.min_cluster_size,
                    "cluster_n_components": args.cluster_dim,
                    "viz_n_components":   args.viz_dim,
                    "random_state":       args.random_state,
                    "cluster_backend_requested": args.cluster_backend,
                    "cluster_backend_selected":  selected_backend,
                    "backend_fallback_reason":   fallback_reason,
                    "output": str(
                        viz_npz_path.relative_to(ROOT)
                        if viz_npz_path.is_relative_to(ROOT)
                        else viz_npz_path
                    ),
                    "interest_state_dir": None
                    if interest_state_dir is None
                    else str(interest_state_dir.relative_to(ROOT) if interest_state_dir.is_relative_to(ROOT) else interest_state_dir),
                    "state_db": str(
                        state_db_path.relative_to(ROOT)
                        if state_db_path.is_relative_to(ROOT)
                        else state_db_path
                    ),
                    "reset_state_db": bool(args.reset_state_db),
                },
            }
        },
    })

    data          = np.load(embeddings_path)
    embeddings    = data["embeddings"]      # (N, 128)
    user_ids      = data["user_ids"]        # (N,)
    # canonical: event_idx / legacy embeddings.npz: timepoint_idx
    timepoint_key = "event_idx" if "event_idx" in data.files else "timepoint_idx"
    timepoint_idx = data[timepoint_key]     # (N,)
    movie_ids_array = data["movie_ids"] if "movie_ids" in data.files else None
    logger.info("Embeddings source: %s | timepoint_key=%s", embeddings_path.name, timepoint_key)
    initial_embedding_rows  = int(embeddings.shape[0])
    initial_unique_users    = int(len(np.unique(user_ids)))
    logger.info("Loaded embeddings: %s | unique users: %d | movie_ids=%s",
                embeddings.shape, initial_unique_users, movie_ids_array is not None)

    nan_mask = np.isnan(embeddings).any(axis=1)
    dropped_nan_rows = int(nan_mask.sum())
    if nan_mask.any():
        logger.warning("Dropping %d NaN rows", dropped_nan_rows)
        embeddings    = embeddings[~nan_mask]
        user_ids      = user_ids[~nan_mask]
        timepoint_idx = timepoint_idx[~nan_mask]
        if movie_ids_array is not None:
            movie_ids_array = movie_ids_array[~nan_mask]

    if args.top_n is not None:
        counts    = {uid: (user_ids == uid).sum() for uid in np.unique(user_ids)}
        top_users = set(sorted(counts, key=counts.get, reverse=True)[:args.top_n])
        mask      = np.isin(user_ids, list(top_users))
        embeddings    = embeddings[mask]
        user_ids      = user_ids[mask]
        timepoint_idx = timepoint_idx[mask]
        if movie_ids_array is not None:
            movie_ids_array = movie_ids_array[mask]
        logger.info("Top-%d users selected", args.top_n)

    if args.user_id is not None:
        mask = user_ids == args.user_id
        if not mask.any():
            logger.error("user_id=%d not found", args.user_id)
            raise SystemExit(1)
        embeddings    = embeddings[mask]
        user_ids      = user_ids[mask]
        timepoint_idx = timepoint_idx[mask]
        if movie_ids_array is not None:
            movie_ids_array = movie_ids_array[mask]
        logger.info("Test mode: user_id=%d | timepoints=%d", args.user_id, mask.sum())

    logger.info(
        "Starting per-user clustering | backend=%s cluster_dim=%d viz_dim=%d "
        "min_cluster_size=%d stride=%d window_size=%d window_step=%d",
        selected_backend, args.cluster_dim, args.viz_dim,
        args.min_cluster_size, args.stride, args.window_size, args.window_step,
    )
    t0 = time.time()
    results = run_per_user_clustering(
        embeddings, user_ids, timepoint_idx,
        movie_ids_array=movie_ids_array,
        backend=selected_backend,
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

    k_values = [r["n_clusters"] for r in results.values()]
    if k_values:
        k_summary = {
            "min":    int(min(k_values)),
            "max":    int(max(k_values)),
            "mean":   float(np.mean(k_values)),
            "median": float(np.median(k_values)),
        }
        logger.info("K distribution | min=%d max=%d mean=%.1f median=%.1f",
                    k_summary["min"], k_summary["max"],
                    k_summary["mean"], k_summary["median"])
    else:
        k_summary = {"min": None, "max": None, "mean": None, "median": None}
        logger.warning("K distribution unavailable: no users clustered")

    save_interest_states(results, interest_state_dir,
                         state_db=state_db_path,
                         run_id=run_id,
                         backend=selected_backend,
                         genre_map_idx=genre_map_idx, all_genres=all_genres,
                         logger=logger)
    runtime_store.checkpoint(state_db_path)
    save_viz_npz(results, viz_npz_path, logger=logger)

    metric_record = {
        "stage":                   "cluster",
        "input_embedding_rows":    initial_embedding_rows,
        "input_unique_users":      initial_unique_users,
        "dropped_nan_rows":        dropped_nan_rows,
        "clustered_users":         int(len(results)),
        "filtered_embedding_rows": int(embeddings.shape[0]),
        "filtered_unique_users":   int(len(np.unique(user_ids))),
        "elapsed_seconds":         float(elapsed_seconds),
        "cluster_backend_selected": selected_backend,
        **{f"k_{k}": v for k, v in k_summary.items()},
    }
    append_metric(run_dir, metric_record)
    update_experiment_manifest(run_dir, {
        "stages": {
            "cluster": {
                "data_summary": {
                    "initial_embedding_rows":  initial_embedding_rows,
                    "initial_unique_users":    initial_unique_users,
                    "dropped_nan_rows":        dropped_nan_rows,
                    "filtered_embedding_rows": int(embeddings.shape[0]),
                    "filtered_unique_users":   int(len(np.unique(user_ids))),
                },
                "outputs": {
                    "state_db": file_metadata(
                        state_db_path, root=ROOT,
                        include_sha256=True,
                        sha256_limit_bytes=hash_limit_bytes,
                    ),
                    "interest_state_dir": None
                    if interest_state_dir is None
                    else str(interest_state_dir.relative_to(ROOT) if interest_state_dir.is_relative_to(ROOT) else interest_state_dir),
                    "viz_npz": file_metadata(
                        viz_npz_path, root=ROOT,
                        include_sha256=True,
                        sha256_limit_bytes=hash_limit_bytes,
                    ),
                    "metrics": file_metadata(
                        run_dir / "metrics.jsonl", root=ROOT,
                        include_sha256=True,
                        sha256_limit_bytes=hash_limit_bytes,
                    ),
                },
                "summary_metrics": metric_record,
            }
        },
    })
