# Docs Index

`docs/`는 GitHub에 남기는 프로젝트 지식 계층이다. `plan/`과 `experiments/`는 계속 로컬 작업장으로 사용하지만, git에는 구조 파일과 템플릿만 유지한다. 새로 clone한 사람이 알아야 하는 내용은 raw 기록을 그대로 올리지 말고 이 디렉터리에 요약한다.

## 읽는 순서

1. `PROJECT_GUIDE.md`: 프로젝트 운영 규칙, 디렉터리 정책, 협업 절차의 정본.
2. `README.md`: 빠른 setup, 주요 명령, 상위 repo map.
3. `docs/current-pipeline-snapshot.md`: 현재 batch/streaming 구현 상태, 알려진 한계, 다음 위험 지점.
4. `docs/data-flow.md`: raw data -> preprocessing -> model -> replay/dashboard 흐름.
5. `docs/artifacts.md`: artifact 소유권, 생성 명령, edit/regenerate 정책.
6. `docs/streaming-e2e-pipeline.md`: streaming/replay e2e 실행과 인계 절차.
7. `docs/streaming-replay-dashboard-contract.md`: replay writer와 dashboard reader 사이 artifact 계약.
8. `docs/part-contracts.md`: schema, preprocessing, model, streaming, replay, dashboard/API/frontend, docs 작업 경계.
9. `dashboard/README.md`, `api/README.md`, `frontend/README.md`: compact dashboard reader별 실행 안내.
10. `docs/decisions/`: 장기적으로 남길 설계/운영 결정.

## 로컬 전용 기록

- `plan/`: active/done/expired 작업 계획. `plan/_template.md`와 상태별 README는 추적하고, 개별 계획서는 로컬 기록으로 둔다.
- `experiments/model/<run_id>/`: run별 `manifest.json`, `metrics.jsonl`, `notes.md`. `experiments/model/README.md`는 추적하고, run 디렉터리는 로컬 기록으로 둔다.
- `outputs/logs/`: 로컬 실행 로그.

로컬 기록에서 미래 협업자가 알아야 할 결론이 생기면 해당 내용을 `docs/`에 요약한다. 전체 manifest, 전체 metric log, 오래된 plan 본문을 그대로 tracked 문서에 복사하지 않는다.

## 로컬 plan 최소 형식

새 계획서는 `plan/_template.md`를 복사해 작성한다. 최소한 아래 항목을 포함한다.

- 목적
- 배경
- 읽어야 할 파일
- 수정 범위와 수정 금지 범위
- 데이터/스키마 영향
- 실행 계획
- 검증
- 완료 조건

## docs에 남길 내용

- 현재 공식 실행 명령과 artifact 경로.
- 파트 사이 input/output 계약.
- 프로젝트 구조나 정책이 그렇게 된 이유.
- 운영 규칙을 바꾸는 대표 검증 결과.
- 다음 작업자가 실행/확장 방식에 영향을 받는 알려진 한계.

## 갱신 기준

아래가 바뀌면 관련 docs를 함께 갱신한다.

- 디렉터리 구조, git 추적 정책, local-only artifact 정책.
- 데이터 스키마, 파일 계약, 전처리 output.
- model/replay/dashboard/API/frontend 실행 명령.
- runtime artifact 이름, stable entrypoint, dashboard reader 계약.
- dependency pin이나 환경 전제.
