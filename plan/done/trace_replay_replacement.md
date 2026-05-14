# Trace Replay Replacement

## 목적

기존 `model.stream.replay_pipeline`의 micro-batch demo runner를 폐기하고, timestamp trace를 N배속 virtual clock 기준으로 재생하는 trace replay runner로 대체한다.

## 배경

현재 replay 구현은 replay input을 고정 크기 micro-batch 파일로 자른 뒤 각 batch마다 CLI subprocess를 순서대로 호출한다. 이 방식은 trace timestamp를 event 단위로 보존하지 못하고, `--replay-speed`도 batch 사이 sleep에만 적용되어 N배속 trace simulation/load test 기준을 만족하지 못한다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `schemas/README.md`
- `model/README.md`
- `docs/current-pipeline-snapshot.md`
- `docs/streaming-e2e-pipeline.md`
- `docs/streaming-replay-dashboard-contract.md`
- `replay/README.md`
- `model/stream/replay_pipeline.py`
- `model/stream/extract_online.py`
- `model/stream/interest_assign.py`
- `model/stream/cluster_refit.py`
- `model/stream/recommend_online.py`

## 수정 범위

- 수정:
  - `model/stream/replay_pipeline.py`
  - `model/stream/trace_replay.py`
  - `docs/streaming-replay-dashboard-contract.md`
  - `docs/streaming-e2e-pipeline.md`
  - `docs/current-pipeline-snapshot.md`
  - `docs/data-flow.md`
  - `docs/artifacts.md`
  - `docs/part-contracts.md`
  - `model/README.md`
  - `model/IMPLEMENTATION_STATUS.md`
  - `replay/README.md`
  - `README.md`
  - `PROJECT_GUIDE.md`
  - `outputs/readme.md`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 기존 사용자 변경의 되돌리기

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: replay artifact가 `micro_batch` 중심에서 `trace_event`/`trace_summary` 중심으로 변경된다.
- 호환성 영향: `python3 -m model.stream.replay_pipeline` 명령은 유지하되 의미를 trace-clock replay runner로 바꾼다. `--micro-batch-size` 중심 운용과 batch files artifact는 제거한다.

## 실행 계획

1. `replay_pipeline.py`를 trace-clock runner로 재작성한다.
   - `--speed N` 기준으로 event별 scheduled wall time을 계산한다.
   - `--replay-speed`는 deprecated alias로만 허용한다.
   - `--micro-batch-size`는 제거하거나 무시하지 않고 CLI에서 없앤다.
2. 처리 경로는 event 단위 schedule을 유지하되, 첫 구현에서는 event가 due 될 때마다 기존 stream CLI를 event JSONL 단위로 호출한다.
   - 이 단계는 trace injection semantics를 바로잡는 것이 우선이다.
   - long-running worker 함수화는 후속 작업으로 분리할 수 있다.
3. `replay_events.jsonl`에 event-level timing metric을 기록한다.
   - `scheduledAt`, `emittedAt`, `processingStartedAt`, `processedAt`
   - `injectorLagSec`, `processingLagSec`, `endToEndLagSec`
   - `behindSchedule`, `queueDepth`
4. `replay_summary.json`에 speed/lag/throughput summary를 기록한다.
5. 문서와 계약을 새 trace replay 기준으로 갱신한다.
6. 작은 smoke로 schedule/summary/기존 stream artifact 생성을 검증한다.

## 검증

- [x] `.venv/bin/python -m py_compile model/stream/trace_replay.py model/stream/replay_pipeline.py dashboard/cluster_dashboard.py`
- [x] `.venv/bin/python -m model.stream.replay_pipeline --help`
- [x] `.venv/bin/python -m model.stream.replay_pipeline --reset-output --generate-events --replay-user-id 28 --limit-events 5 --speed 100 --refit-min-events 3 --assign-trigger-count 3 --outlier-trigger-count 3 --min-cluster-size 2 --cluster-dim 3 --cluster-backend cpu --skip-refit --run-id trace_replay_smoke`
- [x] `outputs/stream/replay_demo/replay_summary.json` status가 `completed`
- [x] `outputs/stream/replay_demo/replay_events.jsonl`에 `stage=trace_event`와 lag metric이 기록됨
- [x] `outputs/stream/replay_demo/ingress_events.jsonl`에 `stream_ingress_event.v1`과 schedule/lag metric이 기록됨
- [x] `git diff --check`

## 완료 조건

- 공식 replay runner가 micro-batch batch-file orchestration이 아니라 trace timestamp N배속 schedule 기준으로 동작한다.
- run summary에서 목표 speed, 실제 emit/process rate, lag, behind schedule 여부를 확인할 수 있다.
- 문서에서 기존 micro-batch replay가 공식 경로로 남아 있지 않다.
