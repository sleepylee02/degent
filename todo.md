# Todo

## Active

- 모델 파트 현황 정리 및 별도 파이프라인 연동 준비
  - owner: sleepylee / LLM
  - files: `model/IMPLEMENTATION_STATUS.md`, `model/README.md`, `PROJECT_GUIDE.md`
  - status: 별도 active plan 없이 `model/IMPLEMENTATION_STATUS.md`로 현재 구현 현황, 산출물 상태, 추후 보류 보완 후보를 정리

## Blocked

- 없음

## Done

- Streaming E2E Handoff Doc
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_e2e_handoff_doc.md`
  - files: `docs/streaming-e2e-pipeline.md`, `docs/data-flow.md`, `docs/part-contracts.md`, `model/README.md`, `todo.md`
  - status: e2e smoke 실행 방법, 현재 검증 결과, 단계별 데이터 전달, artifact map, 기능 확장 지점을 `docs/streaming-e2e-pipeline.md`에 문서화
- Streaming E2E Smoke Stabilization
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_e2e_smoke_stabilization.md`
  - files: `model/stream/cluster_refit.py`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`, `todo.md`
  - status: `--cluster-backend auto`가 CUDA runtime/refit 실패 시 CPU fallback으로 진행되도록 보완. `e2e_streaming_smoke_auto_fallback` replay smoke에서 30/30 events, micro-batch 2개, refit open/closed 2/2, summary completed 확인
- 파트별 협업 계약 문서화
  - owner: sleepylee / LLM
  - files: `docs/part-contracts.md`, `docs/data-flow.md`, `PROJECT_GUIDE.md`, `todo.md`
  - status: 별도 active plan 없이 문서 정리 작업으로 처리. A~G 파트별 담당 파일, input, output, endpoint, 다음 파트로 넘기는 기준을 정리
- Streaming Hybrid Recommendation Pipeline 전환 master plan
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_pipeline.md`
  - files: `plan/done/streaming_pipeline.md`, `todo.md`
  - status: Phase 1~6과 Phase 4-1 완료. Hybrid streaming pipeline은 batch/common/stream 구조, canonical event embedding, online user/interest state, interest assign/refit trigger, GPU-first refit backend, C++ replay engine, replay dashboard reader까지 하나의 replay smoke 흐름으로 검증 완료
- Streaming Pipeline Phase 5: Replay Engine
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_pipeline_phase5_replay_engine.md`
  - files: `replay/`, `model/stream/replay_pipeline.py`, `docs/streaming-replay-dashboard-contract.md`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`, `docs/data-flow.md`, `docs/artifacts.md`, `outputs/readme.md`, `README.md`, `PROJECT_GUIDE.md`, `todo.md`
  - status: C++ replay generator와 Python micro-batch orchestrator 구현 완료. Smoke에서 user 28 events 30, micro-batch 2개, GPU refit request opened/closed 2/2, final active embedding rows 12 확인. 모든 demo artifact는 `outputs/stream/replay_demo/`에 격리
- Streaming Pipeline Phase 6: Replay Dashboard
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_pipeline_phase6_replay_dashboard.md`
  - files: `dashboard/cluster_dashboard.py`, `dashboard/README.md`, `README.md`, `docs/data-flow.md`, `docs/artifacts.md`, `outputs/readme.md`, `todo.md`
  - status: Phase 5 replay artifact를 읽는 read-only dashboard reader 구현 완료. `replay_summary.json` stable entrypoint와 summary `paths` 우선순위, replay events/assignment/refit/interest state view 추가. Phase 5 내부 구현에는 의존하지 않고 `docs/streaming-replay-dashboard-contract.md`만 따른다
- Streaming Pipeline Phase 4-1: GPU-first Clustering/Refit Backend
  - owner: sleepylee / LLM
  - plan: `plan/done/streaming_pipeline_phase4_1_gpu_refit_backend.md`
  - files: `model/stream/cluster_refit.py`, `requirements.txt`, `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`, `docs/data-flow.md`, `docs/artifacts.md`, `outputs/readme.md`, `README.md`, `PROJECT_GUIDE.md`, `todo.md`
  - status: Phase 4 refit request를 소비해 user별 active online embeddings 전체를 refit하고 interest state를 갱신하는 backend 구현 완료. CPU fallback smoke에서 user 28 active 1579 → interest 21, small fixture mean fallback 1 확인. 최종 `.venv` GPU 조합은 `torch==2.5.1+cu121` + RAPIDS/cuML `25.10.0`; `auto` GPU smoke에서 user 28 active 1579 → interest 33, elapsed 약 0.26초 확인
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
