# Streaming Pipeline Phase 5-6: Replay + Dashboard Demo

## 목적

Phase 3~4-1에서 만든 streaming multi-interest update pipeline을 timestamp replay로 한 번에 시현하고, dashboard에서 replay 진행과 interest/refit 상태를 관찰할 수 있게 한다.

Phase 5와 Phase 6은 모델링 아이디어를 바꾸는 작업이 아니라 closed-loop 동작 검증/시현 레이어다.

## 배경

현재 완성된 흐름:

```text
rating event
  -> model.stream.extract_online
  -> model.stream.interest_assign
  -> model.stream.cluster_refit
  -> interest_states/{user_id}.json
```

남은 작업은 실제 timestamp 순서의 event stream을 만들어 위 파이프라인을 micro-batch로 호출하고, 생성된 로그와 상태를 사람이 확인할 수 있게 하는 것이다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `plan/done/streaming_pipeline_phase3_online_embedding_state.md`
- `plan/done/streaming_pipeline_phase4_interest_assign_refit_trigger.md`
- `plan/done/streaming_pipeline_phase4_1_gpu_refit_backend.md`
- `model/stream/extract_online.py`
- `model/stream/interest_assign.py`
- `model/stream/cluster_refit.py`
- `dashboard/cluster_dashboard.py`
- `dashboard/README.md`

## 수정 범위

- 수정/추가:
  - `replay/cpp/rating_replay.cpp`
  - `replay/Makefile`
  - `replay/README.md`
  - `model/stream/replay_pipeline.py`
  - `dashboard/cluster_dashboard.py`
  - `dashboard/README.md`
  - `model/README.md`
  - `model/IMPLEMENTATION_STATUS.md`
  - `docs/data-flow.md`
  - `docs/artifacts.md`
  - `outputs/readme.md`
  - `README.md`
  - `PROJECT_GUIDE.md`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 기존 `outputs/stream/online_embeddings.npz`, `outputs/stream/interest_states/` 기본 산출물 덮어쓰기
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
  - `outputs/stream/replay_demo/interest_assignments.jsonl`
  - `outputs/stream/replay_demo/refit_requests.jsonl`
  - `outputs/stream/replay_demo/refit_events.jsonl`
- 호환성 영향:
  - 기존 Phase 3~4-1 기본 산출물은 보존한다.
  - dashboard는 기존 cluster view에 replay view tab을 추가한다.

## 계약

- C++ replay CLI는 `data/ratings_drop_processed.jsonl`을 읽어 timestamp 정렬 event JSONL을 만든다.
- Python orchestrator는 replay input event를 micro-batch로 읽고 기존 stream CLI를 순서대로 호출한다.
- Phase 6 dashboard는 Python orchestrator 내부 구현을 몰라도 되며, 아래 파일만 읽는다.

```text
outputs/stream/replay_demo/replay_events.jsonl
outputs/stream/replay_demo/replay_summary.json
outputs/stream/replay_demo/interest_assignments.jsonl
outputs/stream/replay_demo/refit_requests.jsonl
outputs/stream/replay_demo/refit_events.jsonl
outputs/stream/replay_demo/interest_states/{user_id}.json
```

## 실행 계획

1. [ ] C++ replay event generator를 구현한다.
2. [ ] Python replay orchestrator를 구현한다.
3. [ ] dashboard에 replay monitoring tab을 추가한다.
4. [ ] 문서와 산출물 계약을 갱신한다.
5. [ ] C++ build, Python py_compile, replay smoke, dashboard import smoke를 검증한다.
6. [ ] 완료 후 계획서를 `plan/done/`으로 이동하고 `todo.md`를 갱신한다.

## 검증

- [ ] `make -C replay`가 C++ replay binary를 만든다.
- [ ] C++ replay CLI가 user 28 이벤트를 timestamp 순서 JSONL로 생성한다.
- [ ] `python3 -m model.stream.replay_pipeline --help`가 실행된다.
- [ ] replay smoke가 `extract_online -> interest_assign -> cluster_refit`을 호출한다.
- [ ] replay summary에 processed event, assignment, refit metric이 기록된다.
- [ ] dashboard module import가 성공한다.

## 완료 조건

- 한 명 이상의 user event stream으로 closed-loop pipeline을 재현할 수 있다.
- replay progress와 refit 결과를 파일 기반으로 dashboard가 읽을 수 있다.
- Phase 5-6은 demo/observability layer로 문서화되고, 추천 scoring/evaluation 범위와 분리된다.
