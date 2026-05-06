# Streaming Pipeline Phase 6: Replay Dashboard

## 목적

Phase 5가 생성한 replay artifact를 읽어 replay 진행, assignment 상태, refit request/close, user별 interest state 변화를 dashboard에서 관찰할 수 있게 한다.

Phase 6은 Phase 5 산출물의 reader이며, replay pipeline을 직접 실행하거나 state를 수정하지 않는다.

## 배경

기존 `dashboard/cluster_dashboard.py`는 cluster result table을 시각화한다. Phase 6은 기존 cluster view를 유지하면서 replay monitoring view를 추가한다.

Phase 6은 `docs/streaming-replay-dashboard-contract.md`의 파일 기반 계약만 의존한다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `docs/streaming-replay-dashboard-contract.md`
- `dashboard/cluster_dashboard.py`
- `dashboard/README.md`
- `docs/data-flow.md`
- `docs/artifacts.md`

## 수정 범위

- 수정/추가:
  - `dashboard/cluster_dashboard.py`
  - `dashboard/README.md`
  - `README.md`
  - `docs/data-flow.md`
  - `docs/artifacts.md`
  - `outputs/readme.md`
  - `todo.md`
- Phase 6이 수정하지 않는 파일:
  - `replay/`
  - `model/stream/replay_pipeline.py`
  - `model/stream/extract_online.py`
  - `model/stream/interest_assign.py`
  - `model/stream/cluster_refit.py`
- 수정 금지:
  - `data/**/raw/`
  - 생성된 replay artifact 직접 편집
  - replay pipeline에서 state를 mutate하는 기능 추가
  - 추천 scoring/evaluation 구현

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 읽기 대상:
  - `outputs/stream/replay_demo/replay_summary.json`
  - `outputs/stream/replay_demo/replay_events.jsonl`
  - `outputs/stream/replay_demo/interest_assignments.jsonl`
  - `outputs/stream/replay_demo/refit_requests.jsonl`
  - `outputs/stream/replay_demo/refit_events.jsonl`
  - `outputs/stream/replay_demo/interest_states/{user_id}.json`
- 생성물 변경:
  - 없음. Dashboard는 reader다.
- 호환성 영향:
  - 기존 cluster dashboard 기능은 유지한다.

## Interface Contract

Phase 6은 `docs/streaming-replay-dashboard-contract.md`를 따라야 한다.

핵심 원칙:

- `replay_summary.json`을 stable entrypoint로 사용한다.
- summary의 `paths`가 있으면 그 경로를 우선 사용하고, 없으면 contract default path를 fallback으로 쓴다.
- optional metric은 없을 수 있으므로 dashboard는 missing column에 tolerant해야 한다.
- replay artifact를 생성/수정/삭제하지 않는다.

## 실행 계획

1. [x] 기존 dashboard에 view selector 또는 tab을 추가한다.
2. [x] Replay Summary view를 추가한다.
   - run status, processed events, unique users, elapsed, throughput
3. [x] Replay Events timeline/table view를 추가한다.
   - batch progress, latency, active embedding rows, assignment status counts
4. [x] Assignment/refit view를 추가한다.
   - assignment status counts
   - open/closed refit requests
   - backend selected, interest count, noise count
5. [x] Interest state browser를 추가한다.
   - userId 선택
   - interest count, pending count, processed count
6. [x] demo artifact가 없을 때도 기존 cluster dashboard가 정상 동작하게 한다.
7. [x] dashboard README와 docs를 갱신한다.

## 검증

- [x] `.venv/bin/python -m py_compile dashboard/cluster_dashboard.py`가 성공한다.
- [x] `streamlit run dashboard/cluster_dashboard.py` 실행 경로가 문서화된다.
- [x] replay artifact가 없어도 dashboard가 에러 없이 기존 cluster view 또는 empty state를 보여준다.
  - 기본 replay summary가 없으면 `Cluster explorer`를 기본 view로 선택한다.
- [x] Phase 5 smoke artifact가 있으면 replay summary/events/refit/interest state가 표시된다.
  - `/tmp` fixture로 `replay_summary.paths`, `replay_events.jsonl`, `interest_states/{user_id}.json` reader helper를 검증했다.
- [x] Dashboard는 `outputs/stream/replay_demo/` 아래 파일을 수정하지 않는다.
  - 구현은 JSON/JSONL/state file read와 Streamlit render만 수행한다.

## 완료 조건

- [x] Phase 5 산출물만으로 replay 진행과 refit 결과를 관찰할 수 있다.
- [x] Phase 6은 Phase 5 내부 구현과 분리되어 병렬 개발 가능하다.
- [x] 기존 cluster dashboard 기능이 깨지지 않는다.
