from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import time

import numpy as np
import pandas as pd
import torch

import model.stream.history_store as history_store
import model.stream.runtime_store as runtime_store
from model.common.runtime import append_metric, load_experiment_manifest, local_timestamp, log_torch_runtime, resolve_torch_device
from model.stream import cluster_refit as refit_stage
from model.stream import extract_online as extract_stage
from model.stream import interest_assign as assign_stage
from model.stream import recommend_online as recommend_stage


def _resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


class ReplayInProcessWorker:
    """Run replay stream stages inside the trace replay process.

    This worker is intentionally replay-scoped. Standalone stage CLIs remain the
    compatibility/debug entrypoints; replay uses this class to avoid reloading
    Python, Torch, movie metadata, item mappings, and checkpoints per event.
    """

    def __init__(
        self,
        *,
        root: Path,
        run_id: str,
        run_dir: Path,
        paths: dict[str, Path],
        args: Any,
        runtime_db_path: Path,
        history_db_path: Path | None,
        seed_state_db_path: Path | None,
        seed_run_id: str | None,
        use_legacy_state_dirs: bool,
        logger: Any,
    ) -> None:
        self.root = root
        self.run_id = run_id
        self.run_dir = run_dir
        self.paths = paths
        self.args = args
        self.runtime_db_path = runtime_db_path
        self.history_db_path = history_db_path
        self.state_db_path = runtime_db_path
        self.seed_state_db_path = seed_state_db_path
        self.seed_run_id = seed_run_id or run_id
        self.user_state_dir = paths["user_state_dir"] if use_legacy_state_dirs else None
        self.interest_state_dir = paths["interest_state_dir"] if use_legacy_state_dirs else None
        self.logger = logger

        self.movies_path = _resolve_path(root, args.movies)
        self.ratings_path = _resolve_path(root, args.ratings)
        self.checkpoint_path = _resolve_path(root, args.checkpoint)
        self.item2idx_path = _resolve_path(root, args.item2idx)

        manifest = load_experiment_manifest(run_dir)
        model_config = manifest.get("stages", {}).get("train", {}).get("model_config", {})
        self.seq_len = int(model_config.get("max_len", 100))
        self.d_model = int(model_config.get("d_model", 128))
        self.num_heads = int(model_config.get("num_heads", 2))
        self.num_layers = int(model_config.get("num_layers", 2))
        self.dropout = float(model_config.get("dropout", 0.2))
        self.positive_policy = extract_stage.PositivePolicy(
            min_ratings_for_zscore=args.min_ratings_for_zscore,
            z_threshold=args.z_threshold,
            optimistic_cold_start=True,
        )

        self._extract_ready = False
        self._cluster_ready = False
        self._recommend_ready = False
        self.projection_context_by_user: dict[int, dict[str, Any]] = {}

    def run_extract_online(self, *, event_id: int, payload: dict[str, Any], replay_order: int | None = None) -> None:
        self._run_stage(
            "extract_online",
            event_id,
            lambda: self._extract_online(event_id=event_id, replay_order=replay_order, payload=payload),
            replay_order=replay_order,
            user_id=int(payload["userId"]),
        )

    def run_interest_assign(self, *, event_id: int, replay_order: int | None = None, user_id: int | None = None) -> None:
        self._run_stage(
            "interest_assign",
            event_id,
            lambda: self._interest_assign(event_id=event_id, replay_order=replay_order),
            replay_order=replay_order,
            user_id=user_id,
        )

    def run_cluster_refit(self, *, event_id: int, user_id: int, replay_order: int | None = None) -> None:
        self._run_stage(
            "cluster_refit",
            event_id,
            lambda: self._cluster_refit(event_id=event_id, replay_order=replay_order, user_id=user_id),
            replay_order=replay_order,
            user_id=user_id,
        )

    def run_recommend_online(self, *, event_id: int, user_id: int, replay_order: int | None = None) -> None:
        self._run_stage(
            "recommend_online",
            event_id,
            lambda: self._recommend_online(event_id=event_id, replay_order=replay_order, user_id=user_id),
            replay_order=replay_order,
            user_id=user_id,
        )

    def _run_stage(
        self,
        stage: str,
        event_id: int,
        fn: Any,
        *,
        replay_order: int | None = None,
        user_id: int | None = None,
    ) -> Any:
        self.logger.info("Running in-process stage: %s eventId=%s", stage, event_id)
        started_at = local_timestamp()
        started = time.time()
        attempt_id = runtime_store.start_stage_attempt(
            self.runtime_db_path,
            run_id=self.run_id,
            event_id=event_id,
            stage=stage,
            command=["in-process", stage],
        )
        try:
            result = fn()
        except Exception as exc:
            runtime_store.finish_stage_attempt(
                self.runtime_db_path,
                attempt_id=attempt_id,
                status="failed",
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
            if self.history_db_path is not None:
                history_store.record_stage_event(
                    self.history_db_path,
                    run_id=self.run_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    user_id=user_id,
                    source_stage=stage,
                    status="failed",
                    started_at=started_at,
                    ended_at=local_timestamp(),
                    latency_sec=time.time() - started,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                )
            raise
        runtime_store.finish_stage_attempt(self.runtime_db_path, attempt_id=attempt_id, status="completed")
        if self.history_db_path is not None:
            history_store.record_stage_event(
                self.history_db_path,
                run_id=self.run_id,
                event_id=event_id,
                replay_order=replay_order,
                user_id=user_id,
                source_stage=stage,
                status="completed",
                started_at=started_at,
                ended_at=local_timestamp(),
                latency_sec=time.time() - started,
            )
        return result

    def _ensure_extract_resources(self) -> None:
        if self._extract_ready:
            return

        self.device, self.device_label = resolve_torch_device()
        log_torch_runtime(self.logger, self.device, self.device_label)

        self.movies = pd.read_csv(self.movies_path)
        self.genre_map, self.all_genres = extract_stage.build_genre_map(self.movies)
        self.num_genres = len(self.all_genres)
        self.logger.info("Loaded movies once: %d | unique genres: %d", len(self.movies), self.num_genres)

        with self.item2idx_path.open("r", encoding="utf-8") as handle:
            self.item2idx = {int(k): int(v) for k, v in json.load(handle).items()}
        self.num_items = len(self.item2idx)
        self.genre_map_idx = {self.item2idx[k]: v for k, v in self.genre_map.items() if k in self.item2idx}
        self.logger.info("Loaded item2idx once: %d items", self.num_items)

        self.extract_model = extract_stage.SASRecCL(
            num_items=self.num_items,
            num_genres=self.num_genres,
            d_model=self.d_model,
            num_heads=self.num_heads,
            num_layers=self.num_layers,
            dropout=self.dropout,
            max_len=self.seq_len,
        )
        self.extract_model.load_state_dict(torch.load(self.checkpoint_path, map_location=self.device, weights_only=True))
        self.extract_model.to(self.device)
        self.logger.info("Loaded extract checkpoint once onto %s", self.device_label)
        self._extract_ready = True

    def _extract_online(self, *, event_id: int, replay_order: int | None, payload: dict[str, Any]) -> None:
        self._ensure_extract_resources()

        event = extract_stage.normalize_event(payload)
        user_id = int(event["user_id"])
        state = extract_stage.load_user_state_with_seed(
            primary_db=self.state_db_path,
            primary_run_id=self.run_id,
            seed_db=self.seed_state_db_path,
            seed_run_id=self.seed_run_id,
            state_dir=self.user_state_dir,
            user_id=user_id,
        )
        if state is None:
            state = extract_stage.make_empty_state(user_id, seq_len=self.seq_len, positive_policy=self.positive_policy)
        else:
            state.positive_policy = self.positive_policy
            state.seq_len = self.seq_len

        extract_stage.append_rating_event(
            state,
            movie_id=int(event["movie_id"]),
            rating=float(event["rating"]),
            rated_at_iso=str(event["rated_at"]),
            item2idx=self.item2idx,
            raw_event_id=None if event.get("raw_event_id") is None else int(event["raw_event_id"]),
        )

        state_path = None
        if self.user_state_dir is not None:
            state_path = extract_stage.save_user_state(state, extract_stage.state_path_for_user(self.user_state_dir, user_id))
        runtime_store.record_user_state(
            self.state_db_path,
            run_id=self.run_id,
            state=state,
            state_path=None if state_path is None else _relative_or_absolute(self.root, state_path),
        )

        signatures = extract_stage.active_embedding_signatures(state, self.seq_len)
        cached_signatures = runtime_store.fetch_active_embedding_cache_signatures(
            self.runtime_db_path,
            run_id=self.run_id,
            user_id=user_id,
        )
        target_raw_event_ids = {
            raw_event_id
            for raw_event_id, signature in signatures.items()
            if cached_signatures.get(raw_event_id) != signature["hash"]
        }
        arrays = extract_stage.extract_state_embeddings(
            state,
            self.extract_model,
            genre_map_idx=self.genre_map_idx,
            num_genres=self.num_genres,
            seq_len=self.seq_len,
            d_model=self.d_model,
            batch_size=self.args.online_batch_size,
            device=self.device,
            target_raw_event_ids=target_raw_event_ids,
        )
        active_before_count = runtime_store.count_active_embedding_cache_rows(
            self.runtime_db_path,
            run_id=self.run_id,
            user_id=user_id,
        )
        cache_summary = runtime_store.upsert_active_embedding_cache(
            self.runtime_db_path,
            run_id=self.run_id,
            user_id=user_id,
            event_id=event_id,
            arrays=arrays,
            signatures=signatures,
            active_raw_event_ids=set(signatures),
        )
        active_after_count = runtime_store.count_active_embedding_cache_rows(
            self.runtime_db_path,
            run_id=self.run_id,
            user_id=user_id,
        )

        validation = extract_stage.validate_online_arrays(arrays, self.seq_len)
        if validation["nan_embedding_rows"]:
            raise ValueError(f"Online embeddings contain {validation['nan_embedding_rows']} NaN rows.")
        if validation["invalid_history_rows"]:
            raise ValueError(f"history_len exceeded seq_len for {validation['invalid_history_rows']} rows.")
        if validation["invalid_context_rows"]:
            raise ValueError(f"context_start_idx > event_idx for {validation['invalid_context_rows']} rows.")
        if not validation["all_rows_active"]:
            raise ValueError("Online embedding output contains non-active rows.")

        changed_cache_rows = runtime_store.fetch_embedding_cache_change_rows(
            self.runtime_db_path,
            run_id=self.run_id,
            event_id=event_id,
        )
        if self.history_db_path is not None:
            state_version = history_store.record_user_state(
                self.history_db_path,
                run_id=self.run_id,
                event_id=event_id,
                replay_order=replay_order,
                state=state,
                changed_raw_event_ids=[int(row["rawEventId"]) for row in changed_cache_rows],
            )
            history_store.record_embedding_changes(
                self.history_db_path,
                run_id=self.run_id,
                event_id=event_id,
                replay_order=replay_order,
                user_id=user_id,
                rows=changed_cache_rows,
                active_before_count=active_before_count,
                active_after_count=active_after_count,
                state_version=state_version,
            )

        if self.args.export_online_embeddings_npz:
            full_arrays = extract_stage.extract_state_embeddings(
                state,
                self.extract_model,
                genre_map_idx=self.genre_map_idx,
                num_genres=self.num_genres,
                seq_len=self.seq_len,
                d_model=self.d_model,
                batch_size=self.args.online_batch_size,
                device=self.device,
            )
            np.savez(self.paths["online_embeddings"], **full_arrays)
            runtime_store.record_embedding_snapshot(
                self.runtime_db_path,
                run_id=self.run_id,
                kind="online_embeddings",
                path=str(self.paths["online_embeddings"]),
                arrays=full_arrays,
                scope="touched_users",
            )

        state_summary = {
            "userId": user_id,
            "stateDb": _relative_or_absolute(self.root, self.state_db_path),
            "statePath": None if state_path is None else _relative_or_absolute(self.root, state_path),
            **(state.stats or {}),
            "embeddingRows": int(arrays["embeddings"].shape[0]),
            "embeddingCache": cache_summary,
        }
        metric_record = {
            "stage": "extract_online",
            "processed_users": 1,
            "input_events": 1,
            "embedding_rows": int(arrays["embeddings"].shape[0]),
            "embedding_dim": int(arrays["embeddings"].shape[1]) if arrays["embeddings"].ndim == 2 else 0,
            "unique_users": int(len(np.unique(arrays["user_ids"]))) if len(arrays["user_ids"]) else 0,
            "cache_embeddings": True,
            "cache_only": not self.args.export_online_embeddings_npz,
            "embedding_cache": cache_summary,
            **validation,
        }
        extract_stage.append_event_log(
            self.paths["online_embedding_events"],
            {
                "recordedAt": local_timestamp(),
                "runId": self.run_id,
                "stage": "extract_online",
                "stateSummaries": [state_summary],
                "metrics": metric_record,
            },
        )
        append_metric(self.run_dir, metric_record)
        self.logger.info("Processed user state: %s", state_summary)

    def _assignment_projection_rows(
        self,
        *,
        user_id: int,
        event_id: int,
        replay_order: int | None,
        assignment_records: list[dict[str, Any]],
        embedding_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not assignment_records:
            return []

        context = self.projection_context_by_user.get(int(user_id))
        rows_by_raw_event_id = {int(row["rawEventId"]): row for row in embedding_rows}
        output: list[dict[str, Any]] = []
        for record in assignment_records:
            raw_event_id = int(record["rawEventId"])
            assignment_status = str(record.get("status", "unknown"))
            assigned_interest_id = record.get("assignedInterestId")
            visual_status = "assigned" if assignment_status == "assigned" else "not_assigned"
            interest_id = assigned_interest_id if visual_status == "assigned" else None
            candidate_interest_id = None if visual_status == "assigned" else assigned_interest_id
            projection_source = "latest_refit"
            umap_x = None
            umap_y = None

            embedding_row = rows_by_raw_event_id.get(raw_event_id)
            if context is None:
                visual_status = "not_projected"
                projection_source = "no_projection_context"
            elif embedding_row is None or embedding_row.get("embedding") is None:
                visual_status = "not_projected"
                projection_source = "missing_embedding_row"
            else:
                try:
                    projected = refit_stage.project_with_refit_detail(
                        np.asarray([embedding_row["embedding"]], dtype=np.float32),
                        context["refitDetail"],
                    )
                    if projected.shape[1] >= 2:
                        umap_x = float(projected[0, 0])
                        umap_y = float(projected[0, 1])
                    elif projected.shape[1] == 1:
                        umap_x = float(projected[0, 0])
                        umap_y = 0.0
                    else:
                        visual_status = "not_projected"
                        projection_source = "empty_projection"
                except Exception as exc:
                    visual_status = "not_projected"
                    projection_source = f"projection_failed:{exc.__class__.__name__}: {exc}"

            output.append(
                {
                    "recordedAt": local_timestamp(),
                    "runId": self.run_id,
                    "eventId": int(event_id),
                    "replayOrder": None if replay_order is None else int(replay_order),
                    "userId": int(user_id),
                    "rawEventId": raw_event_id,
                    "eventIdx": record.get("eventIdx"),
                    "movieId": record.get("movieId"),
                    "assignmentStatus": assignment_status,
                    "visualStatus": visual_status,
                    "interestId": None if interest_id is None else int(interest_id),
                    "candidateInterestId": None if candidate_interest_id is None else int(candidate_interest_id),
                    "similarity": record.get("similarity"),
                    "reason": record.get("reason"),
                    "umapX": umap_x,
                    "umapY": umap_y,
                    "baseStateVersion": None if context is None else context.get("stateVersion"),
                    "baseRefitEventId": None if context is None else context.get("eventId"),
                    "baseRefitReplayOrder": None if context is None else context.get("replayOrder"),
                    "projectionSource": projection_source,
                }
            )
        return output


    def _interest_assign(self, *, event_id: int, replay_order: int | None) -> None:
        rows = runtime_store.fetch_embedding_cache_changed_rows(
            self.runtime_db_path,
            run_id=self.run_id,
            event_id=event_id,
        )
        embedding_dim = assign_stage.embedding_dim_from_rows(rows)
        grouped_rows = assign_stage.group_rows_by_user(rows)
        self.logger.info(
            "Loaded active online embedding rows: %d users=%d dim=%d",
            len(rows),
            len(grouped_rows),
            embedding_dim,
        )

        all_assignment_records: list[dict[str, Any]] = []
        refit_request_records: list[dict[str, Any]] = []
        history_records: list[tuple[int, Any, list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]] = []
        for user_id, user_rows in sorted(grouped_rows.items()):
            state_path = None if self.interest_state_dir is None else assign_stage.state_path_for_user(self.interest_state_dir, user_id)
            state = assign_stage.load_interest_state_with_seed(
                primary_db=self.state_db_path,
                primary_run_id=self.run_id,
                seed_db=self.seed_state_db_path,
                seed_run_id=self.seed_run_id,
                state_dir=self.interest_state_dir,
                user_id=user_id,
            )
            if state is None:
                state = assign_stage.make_empty_interest_state(user_id, embedding_dim)
            if state.embedding_dim != embedding_dim:
                raise ValueError(f"Embedding dim mismatch for user {user_id}: {state.embedding_dim} != {embedding_dim}")

            before_pending = len(state.pending_raw_event_ids)
            before_processed = len(state.processed_raw_event_ids)
            assignment_records, refit_request = assign_stage.assign_user_rows(
                state,
                user_rows,
                similarity_threshold=self.args.similarity_threshold,
                refit_min_events=self.args.refit_min_events,
                assign_trigger_count=self.args.assign_trigger_count,
                outlier_trigger_count=self.args.outlier_trigger_count,
                run_id=self.run_id,
                skip_processed_records=True,
            )
            if state_path is not None:
                assign_stage.save_interest_state(state, state_path)
            runtime_store.record_interest_state(
                self.state_db_path,
                run_id=self.run_id,
                state=state,
                state_path=None if state_path is None else _relative_or_absolute(self.root, state_path),
            )

            all_assignment_records.extend(assignment_records)
            if refit_request is not None:
                refit_request_records.append(refit_request)
            history_records.append((user_id, state, assignment_records, refit_request, user_rows))

            statuses: dict[str, int] = {}
            for record in assignment_records:
                statuses[record["status"]] = statuses.get(record["status"], 0) + 1
            self.logger.info(
                "Processed interest state: %s",
                {
                    "userId": user_id,
                    "inputRows": len(user_rows),
                    "interestCount": len(state.interests),
                    "pendingBefore": before_pending,
                    "pendingAfter": len(state.pending_raw_event_ids),
                    "processedBefore": before_processed,
                    "processedAfter": len(state.processed_raw_event_ids),
                    "refitRequired": state.refit_required,
                    "refitReasons": state.refit_reasons or [],
                    "statuses": statuses,
                    "stateDb": _relative_or_absolute(self.root, self.state_db_path),
                    "statePath": None if state_path is None else _relative_or_absolute(self.root, state_path),
                },
            )

        assign_stage.append_jsonl(self.paths["interest_assignments"], all_assignment_records)
        if refit_request_records:
            assign_stage.append_jsonl(self.paths["refit_requests"], refit_request_records)
        runtime_store.record_assignments(
            self.runtime_db_path,
            run_id=self.run_id,
            records=all_assignment_records,
            event_id=event_id,
        )
        request_ids = runtime_store.open_refit_requests(
            self.runtime_db_path,
            run_id=self.run_id,
            records=refit_request_records,
        )
        request_id_by_user = {
            int(record["userId"]): request_id
            for record, request_id in zip(refit_request_records, request_ids, strict=False)
        }
        if self.history_db_path is not None:
            for history_user_id, state, assignment_records, _refit_request, user_rows in history_records:
                state_version = history_store.record_user_interest_timeline(
                    self.history_db_path,
                    run_id=self.run_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    source_stage="interest_assign",
                    state=state,
                    assignment_records=assignment_records,
                    refit_request_id=request_id_by_user.get(history_user_id),
                )
                history_store.record_assignment_decisions(
                    self.history_db_path,
                    run_id=self.run_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    records=assignment_records,
                    state_version=state_version,
                )
                projection_rows = self._assignment_projection_rows(
                    user_id=history_user_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    assignment_records=assignment_records,
                    embedding_rows=user_rows,
                )
                history_store.record_assignment_projections(
                    self.history_db_path,
                    run_id=self.run_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    rows=projection_rows,
                    state_version=state_version,
                )

        status_counts: dict[str, int] = {}
        for record in all_assignment_records:
            status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1
        append_metric(
            self.run_dir,
            {
                "stage": "interest_assign",
                "input_rows": len(rows),
                "processed_users": len(grouped_rows),
                "assignment_records": len(all_assignment_records),
                "refit_requests": len(refit_request_records),
                "status_counts": status_counts,
                "embedding_dim": embedding_dim,
            },
        )

    def _ensure_cluster_resources(self) -> None:
        if self._cluster_ready:
            return
        if not self._extract_ready:
            self._ensure_extract_resources()
        self.cluster_genre_map = self.genre_map
        self.cluster_all_genres = self.all_genres
        self.selected_backend, self.backend_fallback_reason = refit_stage.choose_backend(self.args.cluster_backend)
        self.logger.info(
            "Cluster backend requested=%s selected=%s fallback_reason=%s",
            self.args.cluster_backend,
            self.selected_backend,
            self.backend_fallback_reason,
        )
        self._cluster_ready = True

    def _cluster_refit(self, *, event_id: int, replay_order: int | None, user_id: int) -> None:
        self._ensure_cluster_resources()

        open_requests = refit_stage.load_open_refit_requests(self.paths["refit_requests"], user_id=user_id)
        runtime_store.open_refit_requests(self.runtime_db_path, run_id=self.run_id, records=list(open_requests.values()))
        request_user_ids = sorted(open_requests)
        self.logger.info("Open refit requests: %d selected=%d", len(open_requests), len(request_user_ids))
        if not request_user_ids:
            append_metric(
                self.run_dir,
                {
                    "stage": "cluster_refit",
                    "open_requests": 0,
                    "selected_requests": 0,
                    "refit_count": 0,
                    "skipped_count": 0,
                    "cluster_backend_requested": self.args.cluster_backend,
                    "cluster_backend_selected": self.selected_backend,
                    "backend_fallback_reason": self.backend_fallback_reason,
                    "elapsed_sec": 0.0,
                    "embedding_dim": 0,
                },
            )
            return

        rows: list[dict[str, Any]] = []
        for request_user_id in request_user_ids:
            rows.extend(
                runtime_store.fetch_active_embedding_cache_rows(
                    self.runtime_db_path,
                    run_id=self.run_id,
                    user_id=request_user_id,
                )
            )
        embedding_dim = int(rows[0]["embedding"].shape[0]) if rows else 0
        grouped_rows = assign_stage.group_rows_by_user(rows)
        self.logger.info("Loaded active online embedding rows: %d users=%d dim=%d", len(rows), len(grouped_rows), embedding_dim)

        refit_events: list[dict[str, Any]] = []
        refit_count = 0
        skipped_count = 0
        started = time.time()
        for request_user_id in request_user_ids:
            request = open_requests[request_user_id]
            request_id = runtime_store.claim_refit_request(self.runtime_db_path, run_id=self.run_id, user_id=request_user_id)
            user_rows = grouped_rows.get(request_user_id, [])
            state_path = (
                None
                if self.interest_state_dir is None
                else assign_stage.state_path_for_user(self.interest_state_dir, request_user_id)
            )
            state = assign_stage.load_interest_state_with_seed(
                primary_db=self.state_db_path,
                primary_run_id=self.run_id,
                seed_db=self.seed_state_db_path,
                seed_run_id=self.seed_run_id,
                state_dir=self.interest_state_dir,
                user_id=request_user_id,
            )
            if state is None:
                state = assign_stage.make_empty_interest_state(request_user_id, embedding_dim)
            if embedding_dim and state.embedding_dim != embedding_dim:
                raise ValueError(
                    f"Embedding dim mismatch for user {request_user_id}: {state.embedding_dim} != {embedding_dim}"
                )

            base_event = {
                "recordedAt": local_timestamp(),
                "runId": self.run_id,
                "userId": request_user_id,
                "request": request,
                "stateDb": _relative_or_absolute(self.root, self.state_db_path),
                "statePath": None if state_path is None else _relative_or_absolute(self.root, state_path),
                "backendRequested": self.args.cluster_backend,
                "backendSelected": self.selected_backend,
                "backendFallbackReason": self.backend_fallback_reason,
                "activeEmbeddingRows": len(user_rows),
            }

            if len(user_rows) < self.args.refit_min_events:
                skipped_count += 1
                event = {
                    **base_event,
                    "status": "skipped",
                    "reason": "insufficient_active_embeddings",
                    "refitMinEvents": self.args.refit_min_events,
                }
                refit_events.append(event)
                state.refit_request_open = False
                state.refit_required = True
                state.updated_at = local_timestamp()
                if state_path is not None:
                    assign_stage.save_interest_state(state, state_path)
                runtime_store.record_interest_state(
                    self.state_db_path,
                    run_id=self.run_id,
                    state=state,
                    state_path=None if state_path is None else _relative_or_absolute(self.root, state_path),
                )
                runtime_store.complete_refit_request(
                    self.runtime_db_path,
                    run_id=self.run_id,
                    user_id=request_user_id,
                    request_id=request_id,
                    status="skipped",
                    payload=event,
                )
                if self.history_db_path is not None:
                    state_version = history_store.record_user_interest_timeline(
                        self.history_db_path,
                        run_id=self.run_id,
                        event_id=event_id,
                        replay_order=replay_order,
                        source_stage="cluster_refit",
                        state=state,
                        refit_request_id=request_id,
                    )
                    history_store.record_refit_lifecycle(
                        self.history_db_path,
                        run_id=self.run_id,
                        event_id=event_id,
                        replay_order=replay_order,
                        user_id=request_user_id,
                        request_id=request_id,
                        status="skipped",
                        state_version=state_version,
                        payload=event,
                    )
                self.logger.info("Skipped refit: %s", event)
                continue

            try:
                user_embeddings = np.stack([row["embedding"] for row in user_rows]).astype(np.float32)
                active_raw_event_ids = [int(row["rawEventId"]) for row in user_rows]
                user_movie_ids = np.array([int(row["movieId"]) for row in user_rows], dtype=np.int64)
                user_start = time.time()
                interests, cluster_summary, actual_backend, actual_fallback_reason, refit_detail = refit_stage.run_refit(
                    user_embeddings,
                    requested_backend=self.args.cluster_backend,
                    selected_backend=self.selected_backend,
                    fallback_reason=self.backend_fallback_reason,
                    min_cluster_size=self.args.min_cluster_size,
                    cluster_dim=self.args.cluster_dim,
                    random_state=42,
                    movie_ids=user_movie_ids,
                    genre_map_idx=self.cluster_genre_map,
                    all_genres=self.cluster_all_genres,
                    logger=self.logger,
                )
            except Exception as exc:
                runtime_store.complete_refit_request(
                    self.runtime_db_path,
                    run_id=self.run_id,
                    user_id=request_user_id,
                    request_id=request_id,
                    status="failed",
                    payload={**base_event, "status": "failed", "error": f"{exc.__class__.__name__}: {exc}"},
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                )
                if self.history_db_path is not None:
                    history_store.record_refit_lifecycle(
                        self.history_db_path,
                        run_id=self.run_id,
                        event_id=event_id,
                        replay_order=replay_order,
                        user_id=request_user_id,
                        request_id=request_id,
                        status="failed",
                        state_version=history_store.get_latest_state_version(
                            self.history_db_path,
                            run_id=self.run_id,
                            user_id=request_user_id,
                        ),
                        payload={**base_event, "status": "failed", "error": f"{exc.__class__.__name__}: {exc}"},
                        error_type=exc.__class__.__name__,
                        error_message=str(exc),
                    )
                raise

            self.selected_backend = actual_backend
            self.backend_fallback_reason = actual_fallback_reason
            base_event["backendSelected"] = self.selected_backend
            base_event["backendFallbackReason"] = self.backend_fallback_reason
            elapsed = time.time() - user_start
            refit_stage.update_state_after_refit(state, interests=interests, active_raw_event_ids=active_raw_event_ids)
            if state_path is not None:
                assign_stage.save_interest_state(state, state_path)
            runtime_store.record_interest_state(
                self.state_db_path,
                run_id=self.run_id,
                state=state,
                state_path=None if state_path is None else _relative_or_absolute(self.root, state_path),
            )
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
            runtime_store.complete_refit_request(
                self.runtime_db_path,
                run_id=self.run_id,
                user_id=request_user_id,
                request_id=request_id,
                status="closed",
                payload=event,
            )
            if self.history_db_path is not None:
                state_version = history_store.record_user_interest_timeline(
                    self.history_db_path,
                    run_id=self.run_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    source_stage="cluster_refit",
                    state=state,
                    refit_request_id=request_id,
                )
                history_store.record_refit_lifecycle(
                    self.history_db_path,
                    run_id=self.run_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    user_id=request_user_id,
                    request_id=request_id,
                    status="closed",
                    state_version=state_version,
                    payload=event,
                )
                history_store.record_interest_vectors(
                    self.history_db_path,
                    run_id=self.run_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    user_id=request_user_id,
                    request_id=request_id,
                    state_version=state_version,
                    state=state,
                )
                membership_rows = history_store.build_membership_rows(
                    user_rows,
                    labels=refit_detail["labels"],
                    z_cluster=refit_detail["zCluster"],
                    label_to_interest_id=refit_detail["labelToInterestId"],
                )
                history_store.record_interest_memberships(
                    self.history_db_path,
                    run_id=self.run_id,
                    event_id=event_id,
                    replay_order=replay_order,
                    user_id=request_user_id,
                    request_id=request_id,
                    state_version=state_version,
                    rows=membership_rows,
                )
                self.projection_context_by_user[request_user_id] = {
                    "stateVersion": state_version,
                    "eventId": event_id,
                    "replayOrder": replay_order,
                    "refitDetail": refit_detail,
                }
            self.logger.info("Closed refit request: %s", event)

        if refit_events:
            refit_stage.append_jsonl(self.paths["refit_events"], refit_events)
        append_metric(
            self.run_dir,
            {
                "stage": "cluster_refit",
                "open_requests": len(open_requests),
                "selected_requests": len(request_user_ids),
                "refit_count": refit_count,
                "skipped_count": skipped_count,
                "cluster_backend_requested": self.args.cluster_backend,
                "cluster_backend_selected": self.selected_backend,
                "backend_fallback_reason": self.backend_fallback_reason,
                "elapsed_sec": time.time() - started,
                "embedding_dim": embedding_dim,
            },
        )

    def _ensure_recommend_resources(self) -> None:
        if self._recommend_ready:
            return
        _, idx2item = recommend_stage.load_item2idx(self.item2idx_path)
        self.recommend_item_embeddings = recommend_stage.load_item_embeddings(
            self.checkpoint_path,
            normalize=self.args.recommend_normalize,
        )
        (
            self.recommend_candidate_indices,
            self.recommend_candidate_movie_ids,
            self.recommend_candidate_vectors,
        ) = recommend_stage.build_candidate_index(self.recommend_item_embeddings, idx2item)
        self.recommend_movie_metadata = recommend_stage.load_movie_metadata(self.movies_path)
        self.logger.info(
            "Loaded recommendation resources once: %d items | candidates: %d",
            self.recommend_item_embeddings.shape[0],
            self.recommend_candidate_movie_ids.shape[0],
        )
        self._recommend_ready = True

    def _recommend_online(self, *, event_id: int, replay_order: int | None, user_id: int) -> None:
        self._ensure_recommend_resources()

        target_user_ids = [int(user_id)]
        interests_by_user, skip_reasons = recommend_stage.load_interest_vectors_from_store(
            state_db=self.state_db_path,
            run_id=self.run_id,
            seed_state_db=self.seed_state_db_path,
            seed_run_id=self.seed_run_id,
            interest_state_dir=self.interest_state_dir,
            user_ids=target_user_ids,
            normalize=self.args.recommend_normalize,
        )
        eligible_users = sorted(interests_by_user)
        if not eligible_users:
            recommend_stage.append_jsonl(self.paths["stream_recommendations"], [])
            recommendation_run_id = runtime_store.record_recommendations(
                self.runtime_db_path,
                run_id=self.run_id,
                records=[],
                event_id=event_id,
                target_user_ids=target_user_ids,
                top_k=self.args.recommend_top_k,
                normalize=self.args.recommend_normalize,
                include_seen=False,
            )
            if self.history_db_path is not None:
                history_store.record_recommendations(
                    self.history_db_path,
                    run_id=self.run_id,
                    recommendation_run_id=recommendation_run_id,
                    records=[],
                    event_id=event_id,
                    replay_order=replay_order,
                    target_user_ids=target_user_ids,
                    top_k=self.args.recommend_top_k,
                    normalize=self.args.recommend_normalize,
                    include_seen=False,
                    trigger_state_versions={
                        target_user_id: history_store.get_latest_state_version(
                            self.history_db_path,
                            run_id=self.run_id,
                            user_id=target_user_id,
                        )
                        for target_user_id in target_user_ids
                    },
                )
            append_metric(
                self.run_dir,
                {
                    "stage": "recommend_online",
                    "target_users": len(target_user_ids),
                    "eligible_users": 0,
                    "skipped_users": len(skip_reasons),
                    "users_with_recommendations": 0,
                    "recommendation_rows": 0,
                    "candidate_items": 0,
                    "top_k": self.args.recommend_top_k,
                    "include_seen": False,
                    "normalize": self.args.recommend_normalize,
                },
            )
            return

        seen_by_user = recommend_stage.load_seen_from_state_store(
            state_db=self.state_db_path,
            run_id=self.run_id,
            seed_state_db=self.seed_state_db_path,
            seed_run_id=self.seed_run_id,
            user_state_dir=self.user_state_dir,
            user_ids=eligible_users,
        )
        now = local_timestamp()
        output_records: list[dict[str, Any]] = []
        users_with_recommendations = 0
        for eligible_user_id in eligible_users:
            rows, _, _, _, _ = recommend_stage.top_recommendations_for_user(
                eligible_user_id,
                interests_by_user[eligible_user_id],
                self.recommend_candidate_indices,
                self.recommend_candidate_movie_ids,
                self.recommend_candidate_vectors,
                top_k=self.args.recommend_top_k,
                seen_movie_ids=seen_by_user.get(eligible_user_id, set()),
            )
            if not rows:
                continue
            users_with_recommendations += 1
            for row in rows:
                meta = self.recommend_movie_metadata.get(int(row["movieId"]), {})
                output_records.append(
                    {
                        "recordedAt": now,
                        "runId": self.run_id,
                        "userId": row["userId"],
                        "rank": row["rank"],
                        "movieId": row["movieId"],
                        "itemIdx": row["itemIdx"],
                        "title": meta.get("title", ""),
                        "releaseYear": meta.get("releaseYear", ""),
                        "genres": meta.get("genres", ""),
                        "ratingAvg": meta.get("ratingAvg", ""),
                        "ratingCount": meta.get("ratingCount", ""),
                        "score": row["score"],
                        "bestClusterId": row["bestClusterId"],
                        "clusterScores": row["clusterScores"],
                        "normalize": self.args.recommend_normalize,
                        "includeSeen": False,
                        "topK": self.args.recommend_top_k,
                    }
                )

        recommend_stage.append_jsonl(self.paths["stream_recommendations"], output_records)
        recommendation_run_id = runtime_store.record_recommendations(
            self.runtime_db_path,
            run_id=self.run_id,
            records=output_records,
            event_id=event_id,
            target_user_ids=eligible_users,
            top_k=self.args.recommend_top_k,
            normalize=self.args.recommend_normalize,
            include_seen=False,
        )
        if self.history_db_path is not None:
            history_store.record_recommendations(
                self.history_db_path,
                run_id=self.run_id,
                recommendation_run_id=recommendation_run_id,
                records=output_records,
                event_id=event_id,
                replay_order=replay_order,
                target_user_ids=eligible_users,
                top_k=self.args.recommend_top_k,
                normalize=self.args.recommend_normalize,
                include_seen=False,
                trigger_state_versions={
                    eligible_user_id: history_store.get_latest_state_version(
                        self.history_db_path,
                        run_id=self.run_id,
                        user_id=eligible_user_id,
                    )
                    for eligible_user_id in eligible_users
                },
            )
        append_metric(
            self.run_dir,
            {
                "stage": "recommend_online",
                "target_users": len(target_user_ids),
                "eligible_users": len(eligible_users),
                "skipped_users": len(skip_reasons),
                "users_with_recommendations": users_with_recommendations,
                "recommendation_rows": len(output_records),
                "candidate_items": int(self.recommend_candidate_movie_ids.shape[0]),
                "top_k": self.args.recommend_top_k,
                "include_seen": False,
                "normalize": self.args.recommend_normalize,
            },
        )
        self.logger.info("Saved %d recommendation rows -> %s", len(output_records), self.paths["stream_recommendations"])
