# Current Pipeline Snapshot

## 목적

현재 batch / streaming 최종 구현 상태를 한 문서로 정리한다. 이 문서는 이후 문제 포인트를 찾고 수정 계획을 세우는 기준 자료로 사용한다.

## 배경

여러 브랜치가 머지되면서 canonical batch extraction, common clustering backend, streaming replay, online recommendation, dashboard artifact reader가 합쳐졌다. 전반적인 흐름은 문서에 분산되어 있으므로 실제 CLI, artifact, state transition, 남은 문제 포인트를 연결해서 다시 정리할 필요가 있다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `docs/data-flow.md`
- `docs/artifacts.md`
- `docs/streaming-e2e-pipeline.md`
- `docs/streaming-replay-dashboard-contract.md`
- `model/batch/*.py`
- `model/stream/*.py`

## 수정 범위

- 수정: `docs/current-pipeline-snapshot.md`, 관련 README/guide 링크, `todo.md`, plan 문서
- 수정 금지: `data/**/raw/`, generated data artifact 직접 편집, pipeline 동작 코드

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: 없음
- 호환성 영향: 없음. 문서 정리만 수행한다.

## 실행 계획

1. 현재 batch pipeline의 CLI, input, output, artifact 계약을 코드 기준으로 확인한다.
2. 현재 streaming pipeline의 event/state/embedding/assign/refit/recommend/replay 흐름을 코드 기준으로 확인한다.
3. 문제 포인트와 바로 이어질 수정 후보를 문서에 정리한다.
4. 관련 문서 목록과 todo/plan 상태를 업데이트한다.

## 검증

- [x] 문서가 현재 코드의 command/path/key 이름과 일치한다.
- [x] stale legacy command를 현재 실행 경로처럼 쓰지 않는다.
- [x] `git diff --check`를 통과한다.

## 완료 조건

- `docs/current-pipeline-snapshot.md`에서 현재 batch / streaming 구현과 streaming 데이터 흐름을 독립적으로 파악할 수 있다.
- 후속 수정 후보가 코드/산출물 단위로 명확히 정리되어 있다.

## 완료 메모

- `docs/current-pipeline-snapshot.md`를 추가해 batch pipeline, streaming data flow, replay orchestration, dashboard connection, 문제 포인트를 한 문서로 정리했다.
- `README.md`, `PROJECT_GUIDE.md`, `model/README.md`, `docs/data-flow.md`에 새 문서 링크와 역할을 반영했다.
- `git diff --check` 통과.
