# Streaming Pipeline Phase 5: Replay Engine

## 목적

ML-32M rating history를 timestamp 순서 event stream으로 변환하고, Phase 3~4-1 streaming pipeline을 micro-batch로 호출해 closed-loop 동작을 재현한다.

Phase 5는 Phase 6 dashboard가 읽을 replay artifact를 쓰는 writer다.

## 배경

Phase 3~4-1은 단일 단계별 CLI로 구현되어 있다.

```text
extract_online -> interest_assign -> cluster_refit
```

Phase 5는 새 모델링을 추가하지 않고, 위 단계를 replay clock 기준으로 묶어 demo/검증 가능한 실행 단위로 만든다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `docs/streaming-replay-dashboard-contract.md`
- `model/stream/extract_online.py`
- `model/stream/interest_assign.py`
- `model/stream/cluster_refit.py`
- `plan/done/streaming_pipeline_phase3_online_embedding_state.md`
- `plan/done/streaming_pipeline_phase4_interest_assign_refit_trigger.md`
- `plan/done/streaming_pipeline_phase4_1_gpu_refit_backend.md`

## 수정 범위

- 수정/추가:
  - `replay/cpp/rating_replay.cpp`
  - `replay/Makefile`
  - `replay/README.md`
  - `model/stream/replay_pipeline.py`
  - `model/README.md`
  - `model/IMPLEMENTATION_STATUS.md`
  - `docs/data-flow.md`
  - `docs/artifacts.md`
  - `outputs/readme.md`
  - `README.md`
  - `PROJECT_GUIDE.md`
  - `todo.md`
- Phase 5가 수정하지 않는 파일:
  - `dashboard/cluster_dashboard.py`
  - `dashboard/README.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 기본 `outputs/stream/*` 산출물 덮어쓰기
  - 추천 scoring/evaluation 구현

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경:
  - `outputs/stream/replay_demo/replay_input_events.jsonl`
  - `outputs/stream/replay_demo/replay_events.jsonl`
  - `outputs/stream/replay_demo/replay_summary.json`
  - `outputs/stream/replay_demo/user_states/{user_id}.json`
  - `outputs/stream/replay_demo/interest_states/{user_id}.json`
  - `outputs/stream/replay_demo/online_embeddings.npz`
  - `outputs/stream/replay_demo/online_embedding_events.jsonl`
  - `outputs/stream/replay_demo/interest_assignments.jsonl`
  - `outputs/stream/replay_demo/refit_requests.jsonl`
  - `outputs/stream/replay_demo/refit_events.jsonl`
- 호환성 영향:
  - Phase 6은 `docs/streaming-replay-dashboard-contract.md`의 파일만 읽는다.
  - 기존 Phase 3~4-1 기본 산출물은 보존한다.

## Interface Contract

Phase 5는 `docs/streaming-replay-dashboard-contract.md`를 따라야 한다.

핵심 원칙:

- 모든 demo 산출물은 `outputs/stream/replay_demo/` 아래에 둔다.
- replay event ordering은 `ratedAtTs`, `userId`, `movieId`, `eventId` 순서다.
- `replay_summary.json`은 Phase 6의 stable entrypoint다.
- `replay_events.jsonl`은 append-only progress log다.
- 계약 변경이 필요하면 코드보다 `docs/streaming-replay-dashboard-contract.md`를 먼저 수정한다.

## 실행 계획

1. [x] C++ replay generator를 구현한다.
   - input: `data/ratings_drop_processed.jsonl`
   - output: `replay_input_events.jsonl`
   - filters: `--user-id`, `--limit-users`, `--limit-events`, optional start/end timestamp
2. [x] `replay/Makefile`과 `replay/README.md`를 추가한다.
3. [x] Python orchestrator `model.stream.replay_pipeline`을 구현한다.
   - C++ output 또는 기존 JSONL input을 micro-batch로 소비
   - batch마다 `extract_online`, `interest_assign`, 필요 시 `cluster_refit` 호출
   - 모든 경로는 `outputs/stream/replay_demo/`로 격리
4. [x] `replay_events.jsonl`과 `replay_summary.json`을 계약대로 기록한다.
5. [x] 문서와 todo를 Phase 5 범위만 갱신한다.

## 검증

- [x] `make -C replay`가 성공한다.
- [x] `replay/bin/rating_replay --help` 또는 동등 CLI help가 실행된다.
- [x] user 28 대상 replay input JSONL이 생성된다.
- [x] `.venv/bin/python -m model.stream.replay_pipeline --help`가 실행된다.
- [x] replay smoke에서 `extract_online -> interest_assign -> cluster_refit` 호출이 끝까지 돈다.
- [x] `replay_summary.json`과 `replay_events.jsonl`이 계약 필드를 포함한다.
- [x] 기존 기본 `outputs/stream/online_embeddings.npz`와 `outputs/stream/interest_states/`를 덮어쓰지 않는다.

검증 명령:

```bash
make -C replay
replay/bin/rating_replay --help
.venv/bin/python -m py_compile model/stream/replay_pipeline.py
.venv/bin/python -m model.stream.replay_pipeline --help
.venv/bin/python -m model.stream.replay_pipeline --reset-output --generate-events --replay-user-id 28 --limit-events 30 --micro-batch-size 15 --refit-min-events 3 --assign-trigger-count 3 --outlier-trigger-count 3 --min-cluster-size 2 --cluster-dim 3 --cluster-backend auto --run-id phase5_replay_smoke
```

Smoke 결과:

- output root: `outputs/stream/replay_demo/`
- input events: 30
- processed events: 30
- unique users: 1
- micro-batches: 2
- refit requests opened/closed: 2 / 2
- final active embedding rows: 12
- `cluster_refit` backend selected: GPU
- elapsed: 약 11.18초

## 구현 결과

- `replay/cpp/rating_replay.cpp`는 `ratings_drop_processed.jsonl`에서 timestamp-sorted replay input JSONL을 생성한다.
- `model/stream/replay_pipeline.py`는 C++ generator를 선택적으로 호출하고, replay input을 micro-batch로 나눠 Phase 3~4-1 CLI를 순서대로 실행한다.
- `--replay-speed 0` 기본값은 빠른 demo를 위해 wall-clock pacing을 끄고, 양수 값은 timestamp gap을 배속으로 압축한다.
- Phase 6은 Phase 5 내부 구현이 아니라 `docs/streaming-replay-dashboard-contract.md`와 `outputs/stream/replay_demo/replay_summary.json`을 기준으로 읽는다.
- Phase 5는 `dashboard/cluster_dashboard.py`, `dashboard/README.md`를 수정하지 않는다.

## 완료 조건

- Phase 3~4-1 closed-loop pipeline을 replay run 하나로 재현할 수 있다.
- Phase 6이 내부 구현 없이 읽을 수 있는 stable artifact set이 생성된다.
- Phase 5 구현은 demo/observability 범위에 머물고 추천 scoring/evaluation을 추가하지 않는다.
