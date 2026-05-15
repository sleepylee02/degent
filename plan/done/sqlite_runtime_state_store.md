# SQLite Runtime State Store

## 목적

로컬 trace replay 파이프라인에서 production streaming 환경에서 생길 법한 resource contention을 관찰할 수 있도록 SQLite 기반 runtime/state store를 도입한다.

현재 상태: 2026-05-15 완료. Trace replay와 stream stage CLI는 `replay.sqlite`에 payload/state/metadata/lifecycle/metric을 dual-write하고, dashboard는 SQLite 우선 reader와 JSONL fallback을 사용한다. `runtime_report`는 DB만 읽어 병목/상태를 요약한다. 대형 vector/checkpoint/NPZ artifact는 파일 정본으로 유지한다. 최종 E2E smoke는 `extract_online -> interest_assign -> cluster_refit -> recommend_online`까지 완료했고 recommendation row 5개를 생성했다.

핵심 목표는 production infra를 만드는 것이 아니다. Kafka/Flink/Postgres/Redis/Kubernetes/vector DB 없이, 현재 로컬 replay에서 아래 질문에 답할 수 있게 만든다.

- event가 빨리 들어올 때 어느 stage가 밀리는가?
- 같은 user state를 계속 갱신할 때 처리 지연이 생기는가?
- embedding/refit 같은 무거운 작업이 replay를 얼마나 늦추는가?
- snapshot 때문에 이미 처리한 event가 반복 처리되는가?
- dashboard가 실행 중 artifact를 안정적으로 읽을 수 있는가?
- refit request가 open/running/closed/skipped/failed/superseded lifecycle로 명확히 추적되는가?

## 배경

현재 trace replay는 `--speed N` 기준 event-level schedule/lag metric을 JSONL에 기록한다. 그러나 runtime state는 여전히 여러 JSON/JSONL/NPZ 파일에 흩어져 있다.

현재 상태를 해석하려면 다음 파일을 함께 읽어야 한다.

- `ingress_events.jsonl`
- `replay_events.jsonl`
- `user_states/{user_id}.json`
- `interest_states/{user_id}.json`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`
- `refit_events.jsonl`
- `stream_recommendations.jsonl`
- `online_embeddings.npz`

이 구조는 debug artifact로는 쓸 수 있지만, "현재 open refit request가 무엇인지", "어떤 user가 어디까지 처리됐는지", "어떤 stage가 병목인지"를 안정적으로 질의하기 어렵다.

따라서 SQLite를 streaming runtime의 state/control-plane source of truth로 도입한다. 대형 vector/data artifact는 파일로 유지하고, SQLite는 payload, state, metadata, lifecycle, runtime metric, file/vector artifact index를 관리한다.

## 저장 원칙

### SQLite 정본

- replay run 상태
- input event와 event progress
- stage attempt, latency, failure, retry
- user raw event state
- user positive event state
- user state payload
- interest state payload
- interest vector
- assignment 결과와 `already_processed`
- refit request lifecycle
- recommendation metadata/result
- runtime metric
- artifact metadata
- embedding snapshot/row index

### 파일 정본

- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `outputs/canonical_embeddings.npz`
- `outputs/stream/.../online_embeddings.npz`
- `outputs/user_interests.npz`
- batch parquet/csv/jsonl 산출물
- 대형 embedding/vector matrix

### Vector 저장 방침

Vector DB는 도입하지 않는다. 현재 목적은 ANN/vector serving이 아니라 local trace replay 관측이다.

- interest vector: 작고 runtime state에 가까우므로 SQLite에 저장한다. 필요하면 `BLOB(float32 bytes)` + metadata로 저장한다.
- online event embedding: 기본 정본은 NPZ 파일이다. SQLite에는 snapshot metadata와 row-level index를 둔다. 작은 smoke용 BLOB 저장은 후속 optional로만 검토한다.
- canonical/batch full embedding matrix: 파일 정본으로 유지하고 SQLite에는 path, shape, dtype, row_count, sha256, row index만 둔다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `schemas/README.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `docs/current-pipeline-snapshot.md`
- `docs/streaming-e2e-pipeline.md`
- `docs/streaming-replay-dashboard-contract.md`
- `docs/artifacts.md`
- `docs/part-contracts.md`
- `dashboard/README.md`
- `model/stream/trace_replay.py`
- `model/stream/extract_online.py`
- `model/stream/interest_assign.py`
- `model/stream/cluster_refit.py`
- `model/stream/recommend_online.py`
- `dashboard/cluster_dashboard.py`

## 수정 범위

- 수정:
  - `model/stream/runtime_store.py`
  - `model/stream/runtime_report.py`
  - `model/stream/trace_replay.py`
  - `model/stream/extract_online.py`
  - `model/stream/interest_assign.py`
  - `model/stream/cluster_refit.py`
  - `model/stream/recommend_online.py`
  - `dashboard/cluster_dashboard.py`
  - `docs/streaming-replay-dashboard-contract.md`
  - `docs/streaming-e2e-pipeline.md`
  - `docs/current-pipeline-snapshot.md`
  - `docs/data-flow.md`
  - `docs/artifacts.md`
  - `docs/part-contracts.md`
  - `model/README.md`
  - `model/IMPLEMENTATION_STATUS.md`
  - `dashboard/README.md`
  - `replay/README.md`
  - `outputs/readme.md`
  - `PROJECT_GUIDE.md`
  - `README.md`
  - `todo.md`
- 선택 수정:
  - `docs/decisions/0004-sqlite-runtime-state-store.md`
  - `tests/` 또는 repo-local smoke validation script가 있다면 추가
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 대형 vector artifact를 git에 추가
  - vector DB, server DB, external queue 도입

## 데이터/스키마 영향

- 컬럼 변경: 기존 CSV/JSONL 스키마 변경 없음
- 생성물 변경:
  - 신규 `outputs/stream/replay_demo/replay.sqlite`
  - `replay_summary.json`의 `paths.replayDb` 추가
  - 기존 JSONL/JSON/NPZ artifact는 fallback/debug/export 용도로 유지
- 호환성 영향:
  - 1차 구현에서는 기존 file artifact를 유지하며 SQLite dual-write를 추가한다.
  - 후속 단계에서 user/interest state payload와 refit lifecycle의 정본을 SQLite로 승격한다.
  - dashboard는 SQLite 우선, 기존 JSONL fallback으로 동작한다.

## SQLite Schema 초안

초기 schema는 migration version을 둔다. `runtime_store.py`는 `PRAGMA user_version` 또는 `schema_meta` table로 버전을 관리한다.

### Core

- `schema_meta`
  - `key`, `value`, `updated_at`
- `runs`
  - `run_id`, `status`, `started_at`, `ended_at`, `speed`, `output_root`, `summary_path`, `created_at`, `updated_at`
- `artifacts`
  - `artifact_id`, `run_id`, `kind`, `path`, `dtype`, `shape_json`, `row_count`, `size_bytes`, `sha256`, `created_at`

### Event / Stage

- `input_events`
  - `run_id`, `event_id`, `replay_order`, `user_id`, `movie_id`, `rating`, `rated_at`, `rated_at_ts`, `source`
- `event_progress`
  - `run_id`, `event_id`, `status`, `scheduled_at`, `emitted_at`, `processing_started_at`, `processed_at`, `injector_lag_sec`, `processing_lag_sec`, `end_to_end_lag_sec`, `behind_schedule`, `failed_attempts`
- `stage_attempts`
  - `attempt_id`, `run_id`, `event_id`, `stage`, `status`, `started_at`, `ended_at`, `latency_sec`, `attempt_no`, `error_type`, `error_message`
- `runtime_metrics`
  - `run_id`, `recorded_at`, `event_id`, `queue_depth`, `refit_backlog`, `open_refit_count`, `running_refit_count`, `throughput_events_per_sec`, `notes_json`

### User State

- `user_states`
  - `run_id`, `user_id`, `version`, `status`, `raw_event_count`, `positive_event_count`, `active_event_count`, `skipped_unknown_items`, `last_raw_event_id`, `payload_json`, `state_path`, `updated_at`
- `user_raw_events`
  - `run_id`, `user_id`, `raw_event_id`, `movie_id`, `rating`, `rated_at`, `rated_at_ts`, `status`, `payload_json`
- `user_positive_events`
  - `run_id`, `user_id`, `raw_event_id`, `event_idx`, `movie_id`, `rating`, `rated_at`, `rated_at_ts`, `status`, `reason`, `z_score`, `payload_json`

### Interest / Assignment

- `interest_states`
  - `run_id`, `user_id`, `version`, `interest_count`, `pending_count`, `processed_count`, `assigned_since_last_refit`, `outlier_since_last_refit`, `refit_required`, `refit_request_open`, `payload_json`, `state_path`, `updated_at`
- `interest_vectors`
  - `run_id`, `user_id`, `interest_id`, `version`, `dim`, `dtype`, `vector_blob`, `assigned_count`, `source`, `top_genres_json`, `created_at`, `updated_at`
- `assignments`
  - `run_id`, `event_id`, `user_id`, `raw_event_id`, `movie_id`, `status`, `interest_id`, `similarity`, `already_processed`, `reason`, `created_at`

### Refit

- `refit_requests`
  - `request_id`, `run_id`, `user_id`, `status`, `opened_at`, `running_at`, `closed_at`, `reasons_json`, `pending_count`, `assigned_since_last_refit`, `outlier_since_last_refit`, `attempt_count`, `superseded_by`, `error_type`, `error_message`
- `refit_attempts`
  - `attempt_id`, `request_id`, `run_id`, `user_id`, `status`, `started_at`, `ended_at`, `latency_sec`, `active_embedding_rows`, `interest_count`, `backend`, `skip_reason`, `error_type`, `error_message`

Allowed lifecycle:

```text
open -> running -> closed
                -> skipped
                -> failed
open -> superseded
running -> failed
```

### Embedding Index

- `embedding_snapshots`
  - `snapshot_id`, `run_id`, `stage_attempt_id`, `kind`, `scope`, `store_mode`, `artifact_id`, `path`, `row_count`, `dim`, `dtype`, `created_at`
- `embedding_rows`
  - `snapshot_id`, `row_idx`, `run_id`, `user_id`, `raw_event_id`, `event_idx`, `movie_id`, `status`, `history_len`, `context_start_idx`, `already_processed`, `artifact_id`, `artifact_row_idx`

### Recommendation

- `recommendation_runs`
  - `recommendation_run_id`, `run_id`, `event_id`, `user_id`, `top_k`, `normalize`, `include_seen`, `started_at`, `ended_at`, `latency_sec`, `row_count`, `status`
- `recommendation_rows`
  - `recommendation_run_id`, `run_id`, `user_id`, `rank`, `movie_id`, `item_idx`, `score`, `best_interest_id`, `metadata_json`

## 실행 계획

## 현재 위치

| phase | 상태 | 요약 |
|---|---|---|
| Phase 0 | 완료 | SQLite는 runtime/state/control-plane 정본, vector DB는 비도입, 대형 vector는 파일 정본으로 문서화 |
| Phase 1 | 완료 | `runtime_store.py` schema/init/upsert/query helper 추가 |
| Phase 2 | 완료 | `trace_replay.py --runtime-db`, `paths.replayDb`, event/stage metric DB 기록 |
| Phase 3 | 완료 | user/interest state, assignment, recommendation metadata DB dual-write |
| Phase 4 | 완료 | refit request `open -> running -> closed/skipped/failed/superseded` lifecycle DB 기록 |
| Phase 5 | 부분 완료 | replay-scoped online embedding snapshot/index 기록. canonical/batch full artifact index는 후속 |
| Phase 6 | 완료 | dashboard SQLite 우선 reader + JSONL fallback |
| Phase 7 | 완료 | 주요 README/docs/current snapshot/contract 업데이트 및 smoke 검증 |
| Phase 8 | 완료 | `runtime_report` markdown/json 병목 report 추가 |
| 최종 E2E | 완료 | 5-event replay + refit + recommend smoke completed, `recommendation_rows=5` |

### Phase 0. 계약/결정 문서화

1. SQLite runtime/state store가 data plane이 아니라 local runtime state/control-plane source of truth임을 문서화한다.
2. Vector DB를 도입하지 않는 결정을 명시한다.
3. 큰 vector matrix는 파일 정본, DB는 metadata/index 정본이라는 원칙을 문서에 반영한다.

### Phase 1. `runtime_store.py` 기반 도입

1. `model/stream/runtime_store.py`를 추가한다.
2. SQLite connection helper를 만든다.
   - `PRAGMA journal_mode=WAL`
   - `PRAGMA busy_timeout`
   - `PRAGMA foreign_keys=ON`
3. schema init/migration helper를 만든다.
4. insert/upsert/query API를 최소 단위로 제공한다.
   - `init_store(path)`
   - `upsert_run(...)`
   - `record_input_event(...)`
   - `record_event_progress(...)`
   - `record_stage_start/end(...)`
   - `record_artifact(...)`
   - `record_runtime_metric(...)`

### Phase 2. Trace replay dual-write

1. `trace_replay.py`에 `--runtime-db` option을 추가한다.
2. 기본값은 `<output-root>/replay.sqlite`로 둔다.
3. replay 시작 시 DB를 초기화하고 `runs` row를 생성한다.
4. event schedule/emit/process metric을 DB에 기록한다.
5. 각 subprocess stage 호출 전후로 `stage_attempts`를 기록한다.
6. `replay_summary.json`에 `paths.replayDb`를 추가한다.
7. 기존 `ingress_events.jsonl`, `replay_events.jsonl`, `replay_summary.json`은 유지한다.

### Phase 3. State payload/summary dual-write

1. `extract_online.py`가 user state 저장 후 DB에 `user_states`, `user_raw_events`, `user_positive_events`를 upsert한다.
2. `interest_assign.py`가 interest state 저장 후 DB에 `interest_states`, `interest_vectors`, `assignments`를 upsert한다.
3. `recommend_online.py`가 recommendation metadata/result를 DB에 기록한다.
4. 기존 JSON state files와 JSONL logs는 유지한다.

### Phase 4. Refit lifecycle DB 정본화

1. `interest_assign.py`가 refit request를 DB에 `open`으로 생성한다.
2. `cluster_refit.py` 또는 `trace_replay.py`가 request를 claim하며 `running`으로 전환한다.
3. refit 결과에 따라 `closed`, `skipped`, `failed`, `superseded`로 전환한다.
4. `refit_requests.jsonl`과 `refit_events.jsonl`은 fallback/debug log로 남긴다.
5. 같은 user의 오래된 open request 처리 정책을 정한다.
   - 새 request가 기존 open request를 대체하면 기존 request는 `superseded`
   - skipped request는 reason에 따라 close 또는 retry 가능 상태로 명확히 기록

### Phase 5. Embedding artifact/index 기록

1. `online_embeddings.npz` 생성 시 `embedding_snapshots`와 `embedding_rows`를 기록한다.
2. `already_processed`와 snapshot 반복 등장 여부를 `embedding_rows` 또는 `assignments`에서 조회 가능하게 한다.
3. canonical/batch artifact는 `artifacts` metadata와 optional row index만 기록한다. 현재 1차 구현에서는 replay-scoped online embedding snapshot metadata/index를 우선 기록하며, canonical/batch full artifact indexing은 후속 확장으로 둔다.
4. online event embedding BLOB 저장은 기본 범위에서 제외한다. 필요하면 후속 `--online-embedding-store sqlite|file|auto`로 분리한다.

### Phase 6. Dashboard SQLite 우선 reader

1. `dashboard/cluster_dashboard.py`가 `replay_summary.json`의 `paths.replayDb`를 먼저 확인한다.
2. DB가 있으면 run/event/stage/refit/assignment/recommendation 상태를 SQLite에서 읽는다.
3. DB가 없으면 기존 JSONL fallback reader를 사용한다.
4. dashboard에 source 표시를 추가한다.
   - `SQLite runtime store`
   - `JSONL fallback`

### Phase 7. 문서/검증 정리

1. `docs/streaming-replay-dashboard-contract.md`에 `replay.sqlite` 계약을 추가한다.
2. `docs/current-pipeline-snapshot.md`와 `docs/streaming-e2e-pipeline.md`를 SQLite runtime store 기준으로 갱신한다.
3. `model/README.md`, `dashboard/README.md`, `outputs/readme.md`, `PROJECT_GUIDE.md`, `README.md`를 갱신한다.
4. smoke run을 실행하고 `replay.sqlite`가 생성되며 dashboard fallback이 유지되는지 확인한다.

### Phase 8. Runtime report/query layer

1. `model/stream/runtime_report.py`를 추가한다.
2. `replay.sqlite`만 읽어 stage latency, event lag, refit lifecycle, assignment/repeated-processing, user state progress를 요약한다.
3. markdown과 JSON 출력 모두 지원한다.
4. 이 report를 다음 representative contention replay의 기본 분석 entrypoint로 사용한다.

## 검증

- [x] `.venv/bin/python -m py_compile model/stream/runtime_store.py model/stream/trace_replay.py model/stream/extract_online.py model/stream/interest_assign.py model/stream/cluster_refit.py model/stream/recommend_online.py dashboard/cluster_dashboard.py`
- [x] `.venv/bin/python -m model.stream.replay_pipeline --help`
- [x] trace replay smoke가 `outputs/stream/replay_demo/replay.sqlite`를 생성한다.
- [x] `replay_summary.json`에 `paths.replayDb`가 기록된다.
- [x] SQLite에서 `runs`, `input_events`, `event_progress`, `stage_attempts`가 조회된다.
- [x] user별 `user_states`, `interest_states`, `assignments`가 조회된다.
- [x] refit request lifecycle이 `open/running/closed/skipped/failed/superseded` 중 하나로 조회된다. 현재 smoke는 `skipped=1` 검증.
- [x] `embedding_snapshots`, `embedding_rows`가 NPZ path/row index를 기록한다.
- [x] dashboard가 SQLite 우선 reader로 동작하고, DB가 없으면 JSONL fallback 경로로 진입한다.
- [x] `.venv/bin/python -m model.stream.runtime_report --db outputs/stream/replay_demo/replay.sqlite --top-events 3`
- [x] `.venv/bin/python -m model.stream.replay_pipeline --reset-output --generate-events --replay-user-id 28 --limit-events 5 --speed 100 --refit-min-events 3 --assign-trigger-count 3 --outlier-trigger-count 3 --min-cluster-size 2 --cluster-dim 3 --cluster-backend cpu --recommend --recommend-top-k 5 --run-id sqlite_runtime_e2e_smoke`
- [x] `git diff --check`

검증 smoke:

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --reset-output \
  --generate-events \
  --replay-user-id 28 \
  --limit-events 3 \
  --speed 100 \
  --refit-min-events 3 \
  --assign-trigger-count 3 \
  --outlier-trigger-count 3 \
  --min-cluster-size 2 \
  --cluster-dim 3 \
  --cluster-backend cpu \
  --run-id sqlite_runtime_refit_smoke
```

주요 DB count:

- `runs=1`, `input_events=3`, `event_progress=3`, `stage_attempts=7`
- `user_states=1`, `user_raw_events=3`, `user_positive_events=3`
- `interest_states=1`, `assignments=5`
- `refit_requests=1`, `refit_attempts=1`, lifecycle `skipped=1`
- `embedding_snapshots=3`, `embedding_rows=5`
- dashboard SQLite reader: `replay_events=3`, `stage_attempts=7`, `assignments=5`, `refit_requests=1`, `refit_events=1`, `recommendations=0`

최종 E2E smoke:

- run id: `sqlite_runtime_e2e_smoke`
- status: `completed`
- input/processed events: 5 / 5
- DB count: `input_events=5`, `event_progress=5`, `stage_attempts=18`, `assignments=10`, `refit_requests=3`, `refit_attempts=3`, `embedding_snapshots=5`, `embedding_rows=10`, `recommendation_runs=5`, `recommendation_rows=5`
- refit lifecycle: `closed=1`, `skipped=2`
- dashboard SQLite reader: `replay_events=5`, `stage_attempts=18`, `assignments=10`, `refit_requests=3`, `refit_events=3`, `recommendations=5`
- note: E2E 중 발견한 두 안정성 이슈도 같이 수정했다.
  - interest vector가 아직 없을 때 `recommend_online`은 실패하지 않고 0-row recommendation run으로 기록한다.
  - small-sample refit에서 UMAP 차원을 안전하게 낮추고, 너무 작은 샘플은 all-noise/mean fallback으로 처리한다.

## 완료 조건

- trace replay run 하나를 `replay.sqlite`만 보고도 run 상태, event progress, stage latency, user state progress, assignment/refit/recommendation 상태를 파악할 수 있다.
- user/interest state payload는 SQLite에 저장되고, 기존 state JSON 파일은 fallback/debug/export artifact로 남는다.
- 대형 vector artifact는 파일 정본으로 유지되고, SQLite는 metadata와 row-level index를 관리한다.
- refit request는 JSONL 해석 없이 SQLite에서 lifecycle 상태를 직접 질의할 수 있다.
- dashboard는 실행 중 artifact를 SQLite 우선으로 안정적으로 읽고, 기존 JSONL fallback을 유지한다.
