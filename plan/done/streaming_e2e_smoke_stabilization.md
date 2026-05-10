# Streaming E2E Smoke Stabilization

## 목적

현재 streaming replay closed-loop를 로컬에서 end-to-end로 다시 실행 가능하게 만든다.

## 배경

문서상 Phase 5 smoke는 완료되어 있었지만, 현재 `--cluster-backend auto` 실행은 cuML import만 보고 GPU를 선택한 뒤 CUDA runtime 실패 시 CPU fallback으로 돌아가지 못했다. 우선 목표는 작은 replay smoke가 `replay_pipeline -> extract_online -> interest_assign -> cluster_refit`까지 완료되도록 하는 것이다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `schemas/README.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `docs/streaming-replay-dashboard-contract.md`
- `replay/README.md`
- `model/stream/replay_pipeline.py`
- `model/stream/cluster_refit.py`

## 수정 범위

- 수정:
  - `model/stream/cluster_refit.py`
  - `model/README.md`
  - `model/IMPLEMENTATION_STATUS.md`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 기존 사용자 변경 파일 되돌리기

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: smoke 실행 시 `outputs/stream/replay_demo/`와 `experiments/model/<run_id>/` 재생성
- 호환성 영향: replay/dashboard 파일 계약 변경 없음

## 실행 결과

1. 현재 replay smoke 실패 원인을 확인했다.
   - `cluster_refit --cluster-backend auto`가 cuML import 성공만 보고 GPU를 선택했다.
   - 실제 refit 실행에서 `cudaErrorInsufficientDriver`가 발생했고 CPU fallback 없이 중단됐다.
2. `cluster_refit --cluster-backend auto`가 CUDA runtime probe를 통과한 경우에만 GPU를 선택하도록 수정했다.
3. `auto` GPU refit 실행 중 예외가 발생하면 CPU backend로 한 번 fallback하도록 수정했다.
4. replay/dashboard artifact 계약은 변경하지 않았다.

## 검증

- [x] `.venv/bin/python -m py_compile model/stream/cluster_refit.py`
- [x] `.venv/bin/python -m model.stream.replay_pipeline --reset-output --generate-events --replay-user-id 28 --limit-events 30 --micro-batch-size 15 --refit-min-events 3 --assign-trigger-count 3 --outlier-trigger-count 3 --min-cluster-size 2 --cluster-dim 3 --cluster-backend auto --run-id e2e_streaming_smoke_auto_fallback`
- [x] `outputs/stream/replay_demo/replay_summary.json` status가 `completed`

## 검증 요약

- input/processed events: 30 / 30
- unique users: 1
- micro-batches: 2
- assignment records: 19
- refit requests opened/closed/skipped: 2 / 2 / 0
- backend selected by refit events: CPU fallback (`cudaErrorInsufficientDriver`)
- elapsed: 약 32.40초

## 완료 조건

- replay closed-loop smoke가 현재 로컬 환경에서 완료된다.
- 실패 원인과 fallback 동작이 문서에 남는다.
