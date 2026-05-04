# Todo

## Active

- 모델 파트 현황 정리 및 별도 파이프라인 연동 준비
  - owner: sleepylee / LLM
  - files: `model/IMPLEMENTATION_STATUS.md`, `model/README.md`, `PROJECT_GUIDE.md`
  - status: 별도 active plan 없이 `model/IMPLEMENTATION_STATUS.md`로 현재 구현 현황, 산출물 상태, 추후 보류 보완 후보를 정리

## Blocked

- 없음

## Done

- LLM 친화적 프로젝트 문서 구조 정리
  - owner: sleepylee / LLM
  - plan: `plan/done/llm_project_structure.md`
  - files: `PROJECT_GUIDE.md`, `README.md`, `todo.md`, `plan/_template.md`, `docs/`, `preprocess/README.md`, `model/README.md`
- 최근 저장소 업데이트 문서 반영
  - owner: sleepylee / LLM
  - plan: `plan/done/refresh_docs_after_repo_updates.md`
  - files: `PROJECT_GUIDE.md`, `README.md`, `docs/`, `preprocess/README.md`, `model/README.md`, `.gitignore`, `todo.md`
- 모델 실험 메타데이터 버저닝
  - owner: sleepylee / LLM
  - plan: `plan/done/model_experiment_versioning.md`
  - files: `model/runtime.py`, `model/train.py`, `model/extract.py`, `model/cluster.py`, `model/README.md`, `PROJECT_GUIDE.md`, `docs/`, `experiments/model/README.md`, `README.md`, `todo.md`

## Coordination Notes

- `data/**/raw/`는 원본 데이터이므로 수정하지 않는다.
- 전처리 생성물 CSV/JSONL은 직접 수정하지 않고 스크립트로 재생성한다.
- 새 작업 전 `PROJECT_GUIDE.md`와 관련 `plan/` 문서를 확인한다.
- 스키마 변경이 필요한 작업은 코드보다 `schemas/`를 먼저 수정한다.
