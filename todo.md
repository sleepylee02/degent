# Todo

## Active

- Interest Scoring Recommendations
  - owner: sleepylee / LLM
  - plan: `plan/active/interest_scoring_recommendations.md`
  - files: `model/batch/recommend.py`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`, `docs/artifacts.md`, `docs/data-flow.md`, `outputs/readme.md`, `todo.md`
  - status: `user_interests.npz` 기반 u_k scoring/recommendation 산출 로직 구현 완료. 로컬에 torch 환경과 최신 `outputs/user_interests.npz`가 없어 실행 smoke는 보류
- Streaming Hybrid Recommendation Pipeline 전환 master plan
  - owner: sleepylee / LLM
  - plan: `plan/active/streaming_pipeline.md`
  - files: `plan/active/streaming_pipeline.md`, `todo.md`
  - status: 전체 coarse plan 등록. 각 phase는 별도 세부 계획 작성 및 승인 후 진행
- 모델 파트 현황 정리 및 별도 파이프라인 연동 준비
  - owner: sleepylee / LLM
  - files: `model/IMPLEMENTATION_STATUS.md`, `model/README.md`, `PROJECT_GUIDE.md`
  - status: 별도 active plan 없이 `model/IMPLEMENTATION_STATUS.md`로 현재 구현 현황, 산출물 상태, 추후 보류 보완 후보를 정리

## Blocked

- 없음

## Done

- Streaming Pipeline Phase 4: Interest Assign / Refit Trigger
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_pipeline_phase4_interest_assign_refit_trigger.md`
  - files: `model/stream/interest_assign.py`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`, `docs/data-flow.md`, `docs/artifacts.md`, `outputs/readme.md`, `README.md`, `PROJECT_GUIDE.md`, `todo.md`
  - status: active positive online embedding을 interest state에 assign하고, interest state가 없거나 pending/outlier/event-count trigger 기준을 넘는 user에 refit request를 남김. smoke에서 no-interest pending 1579/refit request 1, seeded-interest assigned 1579, outlier 1/refit request 확인
- Streaming Pipeline Phase 3: Online Embedding / User Interest State
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_pipeline_phase3_online_embedding_state.md`
  - files: `model/stream/state.py`, `model/stream/extract_online.py`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`, `docs/data-flow.md`, `docs/artifacts.md`, `outputs/readme.md`, `README.md`, `PROJECT_GUIDE.md`, `todo.md`
  - status: raw rating event를 모두 user state에 저장하고 현재까지 관측된 user history 기준 positive projection을 재검증한 뒤 active positive embedding만 생성. user 28 smoke에서 online `(1579, 128)`, NaN 0, invalid context 0, Phase 2 canonical 비교 1579/1579 allclose 확인
- Streaming Pipeline Phase 2: Canonical Event Embedding 전환
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_pipeline_phase2_canonical_embedding.md`
  - files: `model/common/canonical.py`, `model/batch/extract_canonical.py`, `PROJECT_GUIDE.md`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`, `docs/data-flow.md`, `README.md`, `todo.md`
  - status: legacy `model/batch/extract.py`는 보존하고 `python3 -m model.batch.extract_canonical` 경로를 추가. event 하나당 canonical embedding 하나를 보장하며 smoke test에서 `(2869, 128)`, duplicate 0, NaN 0 확인
- Streaming Pipeline Phase 1: 모델 구조 재정리
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_pipeline_phase1_structure.md`
  - files: `model/`, `PROJECT_GUIDE.md`, `README.md`, `docs/data-flow.md`, `model/README.md`, `todo.md`
  - status: `model/batch`, `model/common`, `model/stream` 구조로 분리. 공식 실행 명령은 `python3 -m model.batch.*`. `model/prev/`는 제거하고 과거 모델 정보는 git history, `docs/decisions/`, `experiments/model/`로 추적
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
