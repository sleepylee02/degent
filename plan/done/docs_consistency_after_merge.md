# Docs Consistency After Merge

## 목적

최근 merge로 바뀐 model/stream/dashboard/replay 구조를 현재 운영 문서에 반영한다.

## 배경

`origin/main` 업데이트로 batch/stream 공통 클러스터링, online recommend, replay recommendation output, dashboard recommendation view, cluster export가 추가됐다. 동시에 `model/batch/extract.py`, `model/stream/drift_detector.py`는 현재 git 추적 대상에서 제거됐지만 일부 문서가 아직 현재 entrypoint처럼 참조하고 있었다.

## 읽은 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `docs/data-flow.md`
- `docs/artifacts.md`
- `docs/part-contracts.md`
- `docs/streaming-e2e-pipeline.md`
- `docs/streaming-replay-dashboard-contract.md`
- `dashboard/README.md`
- `outputs/readme.md`
- `replay/README.md`
- `experiments/model/README.md`

## 수정 범위

- 수정:
  - 현재 운영 문서의 파이프라인 흐름, 파일 목록, 산출물 목록, 실행 명령, known gaps
  - 삭제된 entrypoint에 대한 현재형 참조
  - 새 recommend/export/replay/dashboard artifact 계약
- 수정 금지:
  - `data/**/raw/`
  - 재생성 가능한 CSV/JSONL/NPZ/checkpoint 산출물
  - 과거 작업 기록을 보존해야 하는 `plan/done/*`의 역사적 내용

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: 없음
- 호환성 영향: 문서 정합성 보정만 수행

## 실행 결과

1. `PROJECT_GUIDE.md`, `README.md`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`를 최신 모델/stream/replay/recommend 흐름으로 갱신했다.
2. `docs/data-flow.md`, `docs/artifacts.md`, `docs/part-contracts.md`의 input/output/endpoint 계약을 정리했다.
3. `docs/streaming-e2e-pipeline.md`, `docs/streaming-replay-dashboard-contract.md`, `dashboard/README.md`, `outputs/readme.md`, `replay/README.md`에 `--recommend`와 `stream_recommendations.jsonl` 계약을 반영했다.
4. `docs/batch-to-streaming-analysis-v2.md`에는 현재 main 기준 업데이트와 historical context 주석을 추가했다.
5. `todo.md`의 오래된 canonical extract 설명을 현재 main 상태와 맞췄다.

## 검증

- [x] `rg`로 `model.batch.extract`, `batch/extract.py`, `drift_detector.py` 현재형 참조 확인
- [x] `rg`로 `not implemented yet`, `추천 scoring/evaluation은 아직 없다` 등 stale 문구 확인
- [x] `rg`로 conflict marker 확인
- [x] `git diff --check`

## 완료 조건

- 현재 운영 문서가 `extract_canonical -> cluster -> export/recommend`, `extract_online -> interest_assign -> cluster_refit -> recommend_online`, `replay_pipeline --recommend`, dashboard recommendation reader 흐름을 일관되게 설명한다.
- 삭제된 파일은 historical/legacy 맥락에서만 언급된다.
