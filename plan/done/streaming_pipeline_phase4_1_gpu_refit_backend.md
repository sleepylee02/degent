# Streaming Pipeline Phase 4-1: GPU-first Clustering/Refit Backend

## 목적

Phase 4가 기록한 `refit_requests.jsonl`을 소비해 user별 active online embeddings 전체를 다시 clustering하고, 새 interest vectors를 `interest_states/{user_id}.json`에 반영한다.

Phase 4-1 v1은 incremental suffix update가 아니라 refit 대상 user의 active embeddings 전체 재계산으로 간다.

## 배경

Phase 4는 active embedding을 기존 interest vector에 assign하거나, interest state가 없거나 outlier/pending/event-count trigger가 쌓이면 refit request를 기록한다.

```text
outputs/stream/refit_requests.jsonl
  -> Phase 4-1 refit backend
  -> outputs/stream/interest_states/{user_id}.json
```

GPU가 있는 환경에서는 RAPIDS cuML backend를 우선 사용할 수 있게 하되, 개발/로컬 환경을 위해 CPU fallback을 유지한다.

초기 CPU fallback 구현 후 로컬 `.venv`에 cuML을 설치해 실제 GPU smoke까지 확인했다. `cuml-cu12==26.4.0`은 CUDA 12.9 runtime wheel로 인해 로컬 driver/PyTorch cu121 조합에서 실패했고, 최종적으로 RAPIDS/cuML `25.10.0` 계열로 고정했다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `plan/done/streaming_pipeline_phase4_interest_assign_refit_trigger.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `model/batch/cluster.py`
- `model/stream/cluster_refit.py`
- `model/stream/interest_assign.py`

## 수정 범위

- 수정:
  - `model/stream/cluster_refit.py`
  - `model/README.md`
  - `model/IMPLEMENTATION_STATUS.md`
  - `docs/data-flow.md`
  - `docs/artifacts.md`
  - `outputs/readme.md`
  - `README.md`
  - `PROJECT_GUIDE.md`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 기존 `outputs/embeddings.npz`, `outputs/canonical_embeddings.npz`, `outputs/user_interests.npz` 덮어쓰기
  - Phase 5 replay engine 구현

## 결정 사항

- refit 대상 user의 active embeddings 전체를 다시 clustering한다.
- pending suffix만 incremental update하지 않는다.
- `--cluster-backend auto|gpu|cpu`를 제공하고 기본값은 `auto`다.
- `auto`는 cuML UMAP/HDBSCAN import가 가능하면 GPU, 불가능하면 CPU fallback을 사용한다.
- CPU fallback은 기존 batch 방향과 맞춰 `umap-learn + hdbscan`을 사용한다.
- 기본 `refit_min_events`는 20이다.
- Python dependency 변경은 repo-local `.venv`에만 적용한다. 시스템 Python, `sudo pip`, OS package manager, 전역 CUDA/toolkit 설치는 하지 않는다.
- GPU dependency는 `torch==2.5.1+cu121`, RAPIDS/cuML `25.10.0`, `cuda-toolkit==12.1.1`, `cupy-cuda12x==13.6.0`, `scikit-learn==1.7.2` 조합으로 고정한다.
- refit 후 기존 interests는 replace한다.
- refit 후 `pendingRawEventIds`는 clear한다.
- refit 후 `processedRawEventIds`는 active embedding rawEventId 전체로 갱신한다.
- refit 후 `assignedSinceLastRefit`, `outlierSinceLastRefit`은 0으로 초기화한다.
- refit 후 `refitRequired`, `refitRequestOpen`은 false로 닫는다.
- noise label `-1`은 interest vector에서 제외한다.
- 전부 noise면 전체 embedding mean vector를 fallback interest 1개로 만든다.

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경:
  - `outputs/stream/interest_states/{user_id}.json`
  - `outputs/stream/refit_events.jsonl`
- 호환성 영향:
  - Phase 4 `refit_requests.jsonl`과 Phase 3 `online_embeddings.npz`를 입력으로 사용한다.
  - 기존 legacy `outputs/user_interests.npz`는 사용하지 않는다.

## 실행 계획

1. [x] `model/stream/cluster_refit.py`를 구현한다.
   - open refit request 로드
   - active online embedding 로드
   - backend 선택 `auto|gpu|cpu`
   - user별 UMAP + HDBSCAN refit
   - all-noise mean fallback
   - interest state replace/update
   - refit event JSONL 기록
2. [x] run metadata와 metric을 기록한다.
3. [x] 문서를 갱신한다.
4. [x] smoke test를 실행한다.
5. [x] `.venv`에 cuML/RAPIDS GPU dependency를 설치하고 실제 GPU smoke를 실행한다.
6. [x] `requirements.txt`에 최종 `.venv` dependency를 반영한다.
7. [x] GPU dependency 버저닝 결정을 ADR로 남긴다.
8. [x] `todo.md` Phase 4-1 상태를 갱신한다.

## 구현 결과

- 구현 entrypoint:
  - `python3 -m model.stream.cluster_refit`
- 기본 입력:
  - `outputs/stream/online_embeddings.npz`
  - `outputs/stream/refit_requests.jsonl`
- 기본 출력:
  - `outputs/stream/interest_states/{user_id}.json`
  - `outputs/stream/refit_events.jsonl`
- dependency 결정:
  - `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`
  - `requirements.txt`
- smoke run:
  - `experiments/model/phase4_1_cluster_refit_smoke/`
  - `experiments/model/phase4_1_cluster_refit_gpu_smoke/`
  - `experiments/model/phase4_1_cluster_refit_auto_gpu_smoke/`
  - `experiments/model/phase4_1_cluster_refit_auto_gpu_smoke_final/`

구현 중 확인된 사항:

- `auto` backend는 cuML import가 가능하면 GPU를 선택하고, 불가능하면 CPU fallback을 사용한다.
- CPU fallback은 `umap-learn + hdbscan`으로 동작한다.
- `cuml-cu12==26.4.0`은 로컬 driver `535.288.01` / CUDA `12.2` / PyTorch `cu121` 조합에서 실패했다.
- 최종 `.venv`는 RAPIDS/cuML `25.10.0` 계열로 맞췄고 `pip check`, torch CUDA, CuPy kernel, cuML/cudf import, `cluster_refit --cluster-backend auto` GPU smoke를 통과했다.
- 불필요한 CUDA 13 계열 잔여 패키지는 제거했고, 필요한 cu12 NVIDIA shared libraries는 PyTorch cu121 버전으로 복구했다.

## 검증

- [x] `python3 -m model.stream.cluster_refit --help`가 실행된다.
- [x] `--cluster-backend auto`에서 실제 선택 backend가 기록된다.
- [x] open refit request를 읽고 user별 interest state가 갱신된다.
- [x] refit 후 pending/refit flags가 clear된다.
- [x] all-noise 또는 cluster 없는 경우 mean fallback interest가 생성된다.
- [x] `refit_events.jsonl`이 생성된다.
- [x] GPU dependency 조합을 `.venv`에서 검증하고 `requirements.txt`에 반영했다.

검증 기록:

```bash
.venv/bin/python -m py_compile model/stream/cluster_refit.py

.venv/bin/python -m model.stream.cluster_refit --help

.venv/bin/python -m model.stream.cluster_refit \
  --run-id phase4_1_cluster_refit_smoke \
  --embeddings outputs/stream/test_online_embeddings.npz \
  --refit-requests outputs/stream/test_refit_requests_no_interest.jsonl \
  --interest-state-dir outputs/stream/test_interest_states_no_interest \
  --refit-events outputs/stream/test_refit_events_no_interest.jsonl \
  --cluster-backend auto \
  --refit-min-events 20 \
  --min-cluster-size 10 \
  --cluster-dim 10 \
  --user-id 28

.venv/bin/python -m model.stream.cluster_refit \
  --run-id phase4_1_cluster_refit_fallback_smoke \
  --embeddings outputs/stream/test_online_embedding_refit_fallback.npz \
  --refit-requests outputs/stream/test_refit_requests_fallback.jsonl \
  --interest-state-dir outputs/stream/test_interest_states_refit_fallback \
  --refit-events outputs/stream/test_refit_events_fallback.jsonl \
  --cluster-backend cpu \
  --refit-min-events 20 \
  --min-cluster-size 10 \
  --cluster-dim 10 \
  --user-id 2

.venv/bin/pip check

.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"

.venv/bin/python -c "import cupy as cp; x=cp.arange(5, dtype=cp.float32); print(cp.__version__, (x+1).get()); import cuml, cudf; print(cuml.__version__, cudf.__version__)"

.venv/bin/python -m model.stream.cluster_refit \
  --run-id phase4_1_cluster_refit_auto_gpu_smoke_final \
  --embeddings outputs/stream/test_online_embeddings.npz \
  --refit-requests outputs/stream/test_refit_requests_no_interest.jsonl \
  --interest-state-dir outputs/stream/test_interest_states_auto_gpu_final \
  --refit-events outputs/stream/test_refit_events_auto_gpu_final.jsonl \
  --cluster-backend auto \
  --refit-min-events 20 \
  --min-cluster-size 10 \
  --cluster-dim 10 \
  --user-id 28
```

검증 결과:

- CPU fallback smoke: user 28 active rows 1,579, interest 21, noise 43, pending 0, processed 1,579, request closed.
- small fallback fixture: active rows 3, mean fallback interest 1.
- GPU smoke: user 28 active rows 1,579, backend `gpu`, interest 33, noise 124, processed 1,579, elapsed 약 0.26초, request closed.

## 완료 조건

- [x] Phase 4 refit request를 실제 interest state update로 닫을 수 있다.
- [x] GPU-first backend 선택 계약과 CPU fallback이 준비된다.
- [x] Phase 5 replay가 closed-loop pipeline을 호출할 준비가 된다.
