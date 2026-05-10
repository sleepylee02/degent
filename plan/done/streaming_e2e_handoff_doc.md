# Streaming E2E Handoff Doc

## 목적

현재 end-to-end streaming replay 파이프라인이 돌아가는지와, 각 단계에서 어떤 데이터가 넘어가는지를 다른 작업자가 빠르게 공유받을 수 있게 문서화한다.

## 배경

`model.stream.replay_pipeline` smoke는 현재 로컬에서 완료된다. 다음 단계에서는 여러 사람이 같은 실행 경로를 기준으로 기능을 추가해야 하므로, 실행 명령, 입력/출력 artifact, 단계별 데이터 계약, 확장 지점을 명시해야 한다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `docs/data-flow.md`
- `docs/part-contracts.md`
- `docs/streaming-replay-dashboard-contract.md`
- `model/stream/replay_pipeline.py`
- `model/stream/extract_online.py`
- `model/stream/interest_assign.py`
- `model/stream/cluster_refit.py`

## 수정 범위

- 수정:
  - `docs/streaming-e2e-pipeline.md`
  - `docs/data-flow.md`
  - `docs/part-contracts.md`
  - `model/README.md`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - replay demo 생성물 직접 편집

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: 없음
- 호환성 영향: 없음. 기존 replay/dashboard artifact 계약은 유지한다.

## 실행 결과

1. 현재 e2e smoke 상태와 실행 명령을 `docs/streaming-e2e-pipeline.md`에 문서화했다.
2. `replay_input_events -> batch events -> user state -> online_embeddings -> interest_assignments/refit_requests -> refit_events/interest_state -> replay_summary` 흐름을 단계별로 정리했다.
3. 기능 추가자가 지켜야 할 확장 지점과 금지 사항을 정리했다.
4. `docs/data-flow.md`, `docs/part-contracts.md`, `model/README.md`에서 새 문서로 링크했다.

## 검증

- [x] 문서에 현재 smoke 결과와 CPU fallback caveat가 포함되어 있다.
- [x] 문서에 주요 artifact 경로와 필드가 포함되어 있다.
- [x] 기존 파일 계약 문서와 충돌하는 내용이 없다.

## 완료 조건

- 다른 작업자가 `docs/streaming-e2e-pipeline.md`만 읽고 smoke 실행과 단계별 artifact 확인을 시작할 수 있다.
