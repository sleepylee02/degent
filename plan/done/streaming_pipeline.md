# Streaming Hybrid Recommendation Pipeline 전환

상태: 완료. Phase 1~6과 Phase 4-1 세부 계획은 모두 `plan/done/`에 보관한다.

## 목적

현재 ML-32M 기반 batch 추천 파이프라인을 영화 평점 서비스에서 새 평점 이벤트가 계속 들어오는 상황을 가정한 hybrid streaming pipeline으로 확장한다.

이 계획은 전체 방향을 고정하는 master plan이다. 한 번에 전체를 구현하지 않고, 각 phase마다 별도 세부 계획을 작성하고 승인받은 뒤 진행한다.

## 배경

현재 모델 파이프라인은 정적 데이터 기준 batch end-to-end 구조다.

- `model/train.py`: SASRec + Contrastive Loss 학습
- `model/extract.py`: 전체 user history에서 hidden state 일괄 추출
- `model/cluster.py`: user별 UMAP + HDBSCAN으로 multi-interest cluster 추출
- `model/visualize_clusters.py`: cluster 변화 시각화
- `model/runtime.py`: run metadata 기록

실서비스 관점에서는 개별 user가 영화를 자주 평가하지 않으므로 user 단위 full online training은 자연스럽지 않다. 대신 서비스 전체에서는 많은 user의 rating event가 계속 들어오므로, streaming의 단위는 individual user가 아니라 system-level event stream으로 본다.

따라서 모든 단계를 streaming으로 바꾸지 않고, 계산 비용과 갱신 필요성에 따라 cadence를 나누는 hybrid 구조를 목표로 한다.

- 모델 학습은 periodic batch 또는 성능 저하 시 재학습한다.
- 새 이벤트 embedding은 streaming inference로 갱신한다.
- interest assignment는 online update로 처리한다.
- cluster refit은 trigger-based batch로 수행한다.
- replay와 dashboard는 별도 phase에서 붙인다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `model/IMPLEMENTATION_STATUS.md`
- `model/README.md`
- `docs/data-flow.md`
- `dashboard/README.md`
- `plan/_template.md`

## 수정 범위

- 수정:
  - `plan/done/streaming_pipeline.md`
  - `todo.md`
  - 이후 phase별 승인 후 `model/`, `docs/`, `dashboard/`, `PROJECT_GUIDE.md` 등 필요한 파일
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 기존 모델 대형 산출물 직접 편집

## 확정된 설계 결정

- full streaming training이 아니라 hybrid streaming pipeline으로 간다.
- streaming 단위는 individual user가 아니라 system-level rating event stream이다.
- per-user multi-interest clustering을 핵심 구조로 유지한다.
- streaming 평가는 기존 global split보다 chronological replay split을 기준으로 한다.
- refit trigger의 1차 baseline은 event count 기반으로 시작한다.
- batch extract 산출물은 canonical event embedding 계약에 맞게 재생성한다.
- batch 전체 clustering과 trigger-based refit은 GPU-first backend로 전환한다.
- C++ replay engine과 dashboard 연동은 전체 master plan 범위에 포함한다.
- 추천 scoring/evaluation은 당장 선행 구현하지 않고, 담당 작업 또는 별도 phase가 준비되면 붙인다.

## 1차 계획 추가 범위: GPU-first clustering/refit

- 이 1차 총 파이프라인에는 clustering/refit backend를 GPU-first로 전환하는 작업까지 포함한다.
- 대상은 batch 전체 clustering과 streaming trigger에 의해 실행되는 refit이다.
- 기존 per-user UMAP/HDBSCAN 구조는 유지하되, 서버 환경에서는 RAPIDS cuML 같은 GPU backend를 우선 사용한다.
- 로컬/개발 환경과 GPU backend가 준비되지 않은 환경을 위해 CPU fallback은 유지한다.
- 구현 후보는 `--cluster-backend cpu|gpu|auto`이며, 기본 방향은 `auto`에서 CUDA/cuML 사용 가능 시 GPU를 선택하는 것이다.
- 매 이벤트마다 UMAP/HDBSCAN을 refit하지 않는다. Streaming path에서는 online embedding과 interest assignment를 수행하고, refit은 trigger-based batch 작업으로 유지한다.
- 검증 기준은 단순 label 일치가 아니라 runtime, 유저별 K 분포, noise ratio, interest vector 안정성, downstream 추천 지표로 둔다.

## 핵심 계약: Canonical Event Embedding

기존 `extract.py`는 window를 먼저 만들고 그 안에서 여러 시점을 뽑는다. 이 방식에서는 같은 rating event가 여러 window에 들어갈 수 있고, window마다 앞 context 길이와 position이 달라져 같은 event가 여러 embedding을 가질 수 있다.

Streaming pipeline에서는 새 rating event 하나가 들어올 때 hidden state 하나가 생성되어야 하므로, batch extract와 online extract가 같은 embedding 의미를 가져야 한다.

새 계약:

```text
canonical event embedding h_t =
  user의 t번째 positive rating event 직후,
  user의 최근 seq_len개 positive history를 SASRec에 넣고,
  마지막 position의 hidden state를 뽑은 값
```

변경 방향:

```text
기존:
  window를 먼저 만들고 window 안에서 여러 시점을 뽑음
  -> 같은 event가 여러 context로 중복될 수 있음

수정:
  event를 먼저 고르고 event를 마지막 position으로 하는 window 하나를 만듦
  -> 같은 event는 embedding 하나만 가짐
```

검증 기준:

```text
row_count == unique(user_id, event_idx)
```

## 데이터/스키마 영향

- 컬럼 변경:
  - 이 master plan 단계에서는 없음.
  - canonical embedding 산출물 schema는 Phase 2 세부 계획에서 확정한다.
- 생성물 변경:
  - 기존 `outputs/embeddings.npz`, `outputs/user_interests.npz`는 canonical 계약과 맞지 않는 legacy 산출물로 취급한다.
  - 새 산출물은 run별 경로 또는 shard 경로로 저장하는 방향을 Phase 2에서 확정한다.
- 호환성 영향:
  - 기존 batch pipeline은 baseline/legacy 용도로 보존한다.
  - phase별 구현 전 compatibility wrapper 또는 문서화 여부를 별도로 결정한다.

## 실행 계획

각 phase는 아래 순서로 진행한다.

```text
1. phase 세부 계획 작성
2. 사용자 승인
3. 구현
4. 검증
5. phase 완료 기록
6. 다음 phase 세부 계획 작성
```

### Phase 0. Master Plan 등록

- 이 문서를 master plan으로 등록하고 완료 후 `plan/done/streaming_pipeline.md`로 보관한다.
- `todo.md`에서 master plan 상태를 추적한다.
- 전체 방향, 확정 결정, phase 목록, 승인 방식을 기록한다.

### Phase 1. Batch/Stream/Common 구조 재정리

- `model/` 아래에서 batch 실행, streaming 실행, 공통 로직의 경계를 분리한다.
- 기존 batch pipeline은 깨뜨리지 않고 baseline으로 보존한다.
- 예상 구조는 다음을 기준으로 하되, 세부 파일 이동은 Phase 1 계획에서 확정한다.

```text
model/
├── batch/
├── stream/
├── common/
└── README.md
```

### Phase 2. Canonical Event Embedding 전환

- batch extract와 online extract가 공유할 canonical window 생성 계약을 구현한다.
- 기존 overlap-window 기반 중복 embedding 추출을 legacy로 격리하거나 제거한다.
- canonical embedding 산출물과 uniqueness 검증을 추가한다.
- 기존 cluster 산출물은 canonical embedding 기준으로 재생성해야 한다.

### Phase 3. Online Embedding / User Interest State

- 새 rating event와 recent history를 입력받아 canonical hidden state 하나를 생성한다.
- batch extract와 같은 window 생성 함수를 사용한다.
- user별 interest state 저장/로드 계약을 정의한다.
- 학습 vocabulary 밖 item 처리 정책은 세부 계획에서 확정한다.

### Phase 4. Interest Assign / Refit Trigger

- 새 event embedding을 기존 user interest vector `u_k` 중 가장 가까운 interest에 assign한다.
- UMAP/HDBSCAN을 매 이벤트마다 refit하지 않는다.
- 1차 trigger는 event count 기반으로 시작한다.
- 이후 confidence, outlier 비율, 시간 기반, 추천 metric degradation 기반 trigger를 비교 후보로 둔다.

### Phase 4-1. GPU-first Clustering/Refit Backend

- 상태: 완료. 세부 계획은 `plan/done/streaming_pipeline_phase4_1_gpu_refit_backend.md`.
- batch 전체 clustering과 trigger-based refit에 GPU-first backend를 추가한다.
- 기존 CPU `umap-learn` + `hdbscan` 경로는 fallback과 baseline 비교용으로 유지한다.
- GPU 경로는 RAPIDS cuML UMAP/HDBSCAN을 우선 후보로 둔다.
- backend 선택은 `cpu|gpu|auto` 형태로 명시 가능하게 한다.
- `auto`는 CUDA/cuML 사용 가능 시 GPU를 사용하고, 불가능하면 CPU로 fallback한다.
- CPU/GPU 결과 비교는 label 완전 일치가 아니라 runtime, K 분포, noise ratio, interest vector 안정성, downstream 추천 지표를 기준으로 한다.
- 로컬 `.venv`에서는 `torch==2.5.1+cu121` + RAPIDS/cuML `25.10.0` 조합으로 `auto` GPU smoke가 통과했다. GPU dependency 버저닝 결정은 `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`를 따른다.

### Phase 5. C++ Replay Engine

- 상태: 완료. 세부 계획은 `plan/done/streaming_pipeline_phase5_replay_engine.md`.
- ML-32M timestamp를 기준으로 rating event stream을 replay한다.
- C++ `rating_replay`가 timestamp-sorted JSONL을 만들고, Python `model.stream.replay_pipeline`이 micro-batch로 Phase 3~4-1 CLI를 호출한다.
- `--replay-speed`로 replay clock pacing을 지원한다. 기본값 `0`은 빠른 demo/smoke를 위해 wall-clock 대기 없이 처리한다.
- replay clock, throughput, latency, refit trigger 발생 횟수를 기록한다.
- Phase 5는 `outputs/stream/replay_demo/` 아래 replay artifact를 쓰는 writer다.
- Phase 6과의 병렬 구현 계약은 `docs/streaming-replay-dashboard-contract.md`를 따른다.

선택된 연결 방식:

```text
1. C++ replay -> event JSONL/NDJSON 출력 -> Python consumer 처리
2. Python consumer -> micro-batch 단위로 extract_online / interest_assign / cluster_refit CLI 호출
```

### Phase 6. Dashboard 연동

- 상태: 완료. 세부 계획은 `plan/done/streaming_pipeline_phase6_replay_dashboard.md`.
- streaming/refit 결과를 관찰할 수 있도록 dashboard를 확장한다.
- user별 interest state, event stream 진행 상황, assign 결과, refit trigger 발생 시점, cluster 변화, replay 처리량/latency를 표시한다.
- 기존 Streamlit dashboard를 확장할지 별도 dashboard entrypoint를 둘지는 Phase 6 세부 계획에서 결정한다.
- Phase 6은 Phase 5 내부 구현을 호출하지 않고 `outputs/stream/replay_demo/` artifact만 읽는 reader다.
- Dashboard가 기대하는 파일/schema는 `docs/streaming-replay-dashboard-contract.md`에 고정한다.

## 검증

- [x] `plan/done/streaming_pipeline.md`가 완료된 master plan 역할을 한다.
- [x] `todo.md` Done에 이 계획이 연결되어 있다.
- [x] 각 phase가 별도 세부 계획과 승인 후 진행되는 방식이 문서화되어 있다.
- [x] full streaming training을 하지 않는다는 범위가 명확하다.
- [x] canonical event embedding 계약과 기존 overlap-window 방식의 문제가 문서화되어 있다.
- [x] C++ replay engine과 dashboard 연동이 master plan 범위에 포함되어 있다.

최종 통합 검증:

- Phase 1, 2, 3, 4, 4-1, 5, 6 세부 계획이 모두 `plan/done/`에 있다.
- Phase 5 smoke run `phase5_replay_smoke`가 `outputs/stream/replay_demo/`에 계약 artifact를 생성했다.
- Phase 6 dashboard가 `replay_summary.json` stable entrypoint와 summary `paths`를 기준으로 Phase 5 artifact를 read-only로 읽는다.
- 통합 artifact 검증에서 input/processed events `30/30`, micro-batches `2`, assignment records `19`, refit opened/closed/skipped `2/2/0`, final online embedding shape `(12, 128)`, final interest state pending `0`을 확인했다.
- 실행 진입점 검증: `replay/bin/rating_replay --help`, `.venv/bin/python -m model.stream.replay_pipeline --help`, `.venv/bin/python -m py_compile model/stream/replay_pipeline.py dashboard/cluster_dashboard.py`, Streamlit health check.

## 완료 조건

- [x] Master plan이 완료 계획으로 보관된다.
- [x] `todo.md`에서 Done 상태로 추적 가능하다.
- [x] Phase 1~6 구현, 검증, 문서 갱신이 완료되어 hybrid streaming/replay/dashboard 흐름을 하나의 smoke run으로 재현할 수 있다.
