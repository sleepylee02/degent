# Batch to Streaming 전환 분석 v2

이 문서는 `streaming` 브랜치가 기존 batch 추천 파이프라인을 어떤 방식으로 end-to-end streaming pipeline으로 확장했는지 정리한다.

2026-05-14 현재 `origin/main` 기준으로는 이후 merge에서 공통 clustering backend, batch cluster export, streaming online recommendation, replay recommendation artifact, dashboard recommendation view가 추가됐다. 또한 `model/batch/extract.py`와 `model/stream/drift_detector.py`는 현재 git 추적 대상에서 제거됐으므로 이 문서에서 해당 이름이 나오는 부분은 historical context로만 읽는다. 현재 실행 가능한 경로의 정본은 `PROJECT_GUIDE.md`, `model/README.md`, `docs/data-flow.md`, `docs/streaming-e2e-pipeline.md`다.

핵심은 기존 batch를 버린 것이 아니다. 기존 batch가 잘하던 부분은 그대로 재사용하고, streaming에서 반드시 필요한 부분만 새로 정의했다.

```text
이 브랜치의 답:
  모델 학습은 batch로 유지한다.
  full online training은 이번 범위에서 deferred로 둔다.
  새 rating event 처리는 streaming inference와 online state로 처리한다.
  interest update는 online assignment와 trigger-based refit으로 나눈다.
  전체 흐름은 timestamp replay와 dashboard artifact로 검증한다.
```

따라서 full online training은 이번 브랜치 범위에서 deferred이고, 이 브랜치는 **batch-trained SASRec+CL을 기반으로 한 hybrid streaming inference/refit pipeline**이다.

---

## 1. 기존 Batch 파이프라인

기존 구조는 정적 ML-32M 데이터를 한 번에 처리하는 batch workflow였다.

```text
data/ratings_drop_processed.jsonl
  -> user별 positive sequence 생성
  -> SASRec + Contrastive Loss 학습
  -> outputs/sasrec_cl.pt
  -> outputs/item2idx.json
  -> canonical hidden state 일괄 추출
  -> outputs/canonical_embeddings.npz
  -> user별 UMAP + HDBSCAN clustering
  -> outputs/user_interests.npz
  -> outputs/batch/interest_states/{user_id}.json
  -> visualization/dashboard/recommendation 후보
```

현재 브랜치에서는 이 기존 batch 책임을 `model/batch/` 아래에 보존했다.

| 책임 | 현재 파일 | 기존 방식 |
|---|---|---|
| 학습 | `model/batch/train.py` | 전체 전처리 데이터를 읽어 SASRec+CL checkpoint 생성 |
| hidden state 추출 | `model/batch/extract_canonical.py` | event당 canonical hidden state 1개 추출 |
| legacy hidden state 추출 | 제거됨 | 과거 `model/batch/extract.py`가 overlap sliding window 산출물을 생성 |
| clustering | `model/batch/cluster.py` | user별 UMAP + HDBSCAN으로 interest vector/state 생성 |
| dashboard export | `model/batch/export_clusters.py` | `user_interests.npz`를 dashboard table로 변환 |
| recommendation | `model/stream/recommend_online.py` | streaming interest state 기반 top-K 추천 생성 |
| 시각화 | `model/batch/visualize_clusters.py` | `outputs/user_interests.npz`를 user별 plot으로 변환 |

기존 batch는 다음 전제를 가진다.

- 전체 user history가 이미 파일로 존재한다.
- user sequence는 batch 실행 시점에 고정된다.
- positive interaction은 user rating z-score 기준 `z > 0`이다.
- SASRec hidden state를 한 번에 추출한다.
- UMAP/HDBSCAN은 추출된 hidden state 전체를 대상으로 batch 실행한다.

이 방식은 연구 실험에는 단순하고 좋다. 하지만 streaming 서비스 형태로 바로 쓰기에는 문제가 있다.

| 문제 | 이유 |
|---|---|
| 새 event 하나마다 전체 extract/cluster를 다시 돌릴 수 없음 | latency와 비용이 맞지 않음 |
| `outputs/embeddings.npz`는 append-friendly event log가 아님 | batch snapshot에 가까움 |
| 같은 event가 여러 embedding을 가질 수 있음 | overlap window마다 context와 position이 다름 |
| raw event state가 없음 | positive projection을 나중에 다시 계산하기 어려움 |
| `outputs/user_interests.npz`는 정적 snapshot | online assignment/refit 상태를 표현하기 어려움 |
| replay entrypoint가 없음 | timestamp event stream으로 end-to-end 검증하기 어려움 |

그래서 streaming 브랜치의 첫 의도는 "batch를 폐기"가 아니라 "batch 산출물을 streaming에 맞는 계약으로 감싸기"였다.

---

## 2. 전환의 가장 큰 설계 결정

`plan/done/streaming_pipeline.md`의 핵심 결정은 두 가지다.

```text
full streaming training은 deferred로 두고 hybrid streaming pipeline으로 간다.
streaming 단위는 individual user가 아니라 system-level rating event stream이다.
```

개별 user는 영화를 자주 평가하지 않는다. user 단위로 online learning을 계속 돌리는 것은 데이터 빈도와 비용 측면에서 맞지 않는다. 대신 서비스 전체에서는 많은 user의 rating event가 계속 들어온다고 본다.

그래서 이 브랜치는 update cadence를 나눴다.

| 단계 | cadence | 구현 | 의도 |
|---|---|---|---|
| 모델 학습 | periodic batch | `model/batch/train.py` | 기존 SASRec+CL checkpoint를 만든다 |
| canonical batch extract | offline contract check | `model/batch/extract_canonical.py` | batch와 stream embedding 의미를 맞춘다 |
| online embedding | streaming inference | `model/stream/extract_online.py` | 새 event를 state에 반영하고 active embedding을 만든다 |
| interest assignment | online | `model/stream/interest_assign.py` | cosine nearest-interest만 계산한다 |
| cluster refit | trigger-based batch | `model/stream/cluster_refit.py` | UMAP/HDBSCAN은 필요할 때만 수행한다 |
| replay | trace-clock simulation | `replay/cpp/rating_replay.cpp`, `model/stream/trace_replay.py`, `model/stream/replay_pipeline.py` | historical ratings를 `--speed N` virtual clock으로 재생한다 |
| dashboard | read-only monitor | `dashboard/cluster_dashboard.py` | replay artifact를 관찰한다 |

즉 이 브랜치가 낸 답은 다음이다.

```text
모델은 매 event마다 학습하지 않는다.
새 event마다 필요한 representation과 state만 갱신한다.
비싼 clustering은 trigger가 열릴 때만 다시 한다.
```

---

## 3. 기존 Batch에서 그대로 활용한 것

### 3-1. SASRecCL 모델과 checkpoint

기존 모델 구조는 `model/common/sasrec.py`로 이동했다. Streaming path도 새 모델을 만들지 않는다.

그대로 활용하는 것:

- `SASRecCL`
- item embedding
- genre embedding
- positional embedding
- causal Transformer encoder
- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`

Streaming이 새로 하는 일은 checkpoint를 다시 학습하는 것이 아니라, 같은 checkpoint를 online event context에 적용하는 것이다.

```text
기존 batch:
  SASRecCL 학습 후 hidden state batch extract

streaming:
  같은 SASRecCL checkpoint로 user state의 active positive sequence를 inference
```

### 3-2. Positive interaction 정책

기존 batch는 rating z-score 기준으로 positive interaction을 정의했다.

```text
z = (rating - user_mean) / user_std
positive = z > 0
```

Streaming도 이 정책을 버리지 않는다. 대신 raw rating event를 모두 저장하고, 현재까지 관측된 user history 기준으로 positive projection을 다시 만든다.

| 구분 | 기존 batch | streaming |
|---|---|---|
| positive 계산 시점 | 파일 로드 시 한 번 | event ingest 후 user state 기준 재계산 |
| raw rating 보존 | 전처리 JSONL | `user_states/{user_id}.json` |
| cold-start | 크게 고려하지 않음 | rating 수 부족 또는 std 0이면 optimistic positive |
| unknown item | vocabulary 밖이면 모델 입력에서 제외 | raw에는 보존, embedding은 `skipped_unknown` |

### 3-3. UMAP + HDBSCAN multi-interest 구조

기존 batch clustering은 user별 hidden state를 UMAP으로 축소하고 HDBSCAN으로 cluster를 만든 뒤, cluster별 평균 hidden vector를 interest vector로 삼았다.

Streaming refit도 같은 철학을 유지한다.

```text
active online embeddings
  -> UMAP
  -> HDBSCAN
  -> cluster별 mean vector
  -> user interest vectors
```

달라진 것은 입력과 실행 시점이다.

| 항목 | 기존 batch | streaming refit |
|---|---|---|
| 입력 | `outputs/embeddings.npz` | `outputs/stream/online_embeddings.npz` |
| 실행 대상 | 전체 또는 selected users | open refit request user |
| 결과 | `outputs/user_interests.npz` | `interest_states/{user_id}.json` |
| 실행 시점 | 수동 batch | trigger-based |
| backend | CPU | GPU-first `auto`, CPU fallback |

---

## 4. 가장 중요한 문제: 기존 Hidden State Extract는 Streaming과 맞지 않음

기존 `model/batch/extract.py`는 window를 먼저 만들고, 그 window 내부에서 여러 hidden state를 뽑았다.

```text
user sequence
  -> sliding windows
  -> SASRec encode
  -> interval마다 hidden state 추출
  -> outputs/embeddings.npz
```

이 방식은 batch clustering에는 쓸 수 있다. 하지만 streaming event embedding으로는 문제가 있다.

같은 event가 여러 window에 들어갈 수 있기 때문이다.

```text
같은 user event
  -> window A에서는 position 20
  -> window B에서는 position 30
  -> 앞 context도 다름
  -> positional embedding도 다름
  -> hidden state가 달라짐
```

`model/IMPLEMENTATION_STATUS.md`는 legacy extract 산출물에서 이런 현상을 기록한다.

```text
row count: 763,772
unique (user_id, timepoint_idx): 81,968
duplicate rows: 681,804
동일 (user_id, timepoint_idx)의 최대 중복: 10
같은 timepoint의 두 vector L2 distance 예: 약 0.378
```

Streaming에서는 event 하나가 들어오면 embedding 하나가 나와야 한다. 같은 event가 context에 따라 여러 vector를 가지면 online state와 replay 검증이 흔들린다.

그래서 이 브랜치는 기존 `extract.py`를 고치지 않고 새 계약을 추가했다.

---

## 5. Canonical Event Embedding

Streaming 전환의 중심 계약은 **canonical event embedding**이다.

정의:

```text
canonical event embedding h_t =
  user의 t번째 positive rating event 직후,
  user의 최근 seq_len개 positive history를 SASRec에 넣고,
  마지막 non-padding position의 hidden state를 뽑은 값
```

기존 방식과 비교하면 다음과 같다.

| 항목 | 기존 `batch/extract.py` | canonical extract |
|---|---|---|
| 먼저 고르는 것 | window | event |
| row 의미 | window-context 안의 timepoint hidden state | event 직후 canonical hidden state |
| 중복 가능성 | 있음 | `(user_id, event_idx)` unique |
| padding | 기존 dataset은 left-padding | canonical dataset은 right-padding |
| hidden 선택 | interval index | 마지막 non-padding position |
| 산출물 | `outputs/embeddings.npz` | `outputs/canonical_embeddings.npz` |

구현은 `model/common/canonical.py`에 있다.

```python
def build_canonical_item_window(events, event_position, seq_len):
    context_start_position = max(0, event_position - seq_len + 1)
    context = events[context_start_position : event_position + 1]
    return [event.item_idx for event in context], context[0].event_idx, len(context)
```

핵심은 event를 마지막 valid position으로 만드는 것이다.

```text
event_idx=0, seq_len=100
  item_id_seq = [event0, 0, 0, 0, ...]
  get_last_hidden -> index 0 선택

event_idx=99
  item_id_seq = [event0, ..., event99]
  get_last_hidden -> index 99 선택

event_idx=100
  item_id_seq = [event1, ..., event100]
  get_last_hidden -> index 99 선택
```

이 때문에 right-padding이 필요하다. early event도 NaN 없이 hidden state를 만들 수 있고, history가 길어지면 최근 `seq_len`개만 사용한다.

Canonical extract가 추가한 metadata:

```text
embeddings
user_ids
event_idx
movie_ids
rated_at_ts
rated_at_iso
history_len
context_start_idx
```

검증 invariant:

```text
row_count == unique(user_id, event_idx)
history_len <= seq_len
context_start_idx <= event_idx
nan_embedding_rows == 0
```

Phase 2 smoke:

```text
outputs/test_canonical_embeddings.npz
embedding shape: (2869, 128)
unique (user_id, event_idx): 2869 / 2869
NaN rows: 0
max history_len: 100
skipped_unknown_items: 0
```

이 단계가 낸 답:

```text
Batch와 stream이 같은 representation 의미를 공유하려면
event 하나당 embedding 하나라는 canonical contract가 필요하다.
```

---

## 6. Phase 1: 디렉토리와 책임 분리

기존 `model/`은 batch script와 공통 코드가 섞여 있었다.

```text
model/train.py
model/extract.py
model/cluster.py
model/visualize_clusters.py
model/dataset.py
model/model.py
model/runtime.py
```

Phase 1은 구조를 먼저 나눴다.

```text
model/
  batch/
    train.py
    extract_canonical.py
    cluster.py
    export_clusters.py
    recommend.py
    visualize_clusters.py
  common/
    dataset.py
    sasrec.py
    runtime.py
    canonical.py
    cluster.py
  stream/
    state.py
    extract_online.py
    interest_assign.py
    cluster_refit.py
    recommend_online.py
    replay_pipeline.py
```

의도:

| 디렉토리 | 의도 |
|---|---|
| `batch/` | 기존 batch entrypoint 보존 |
| `common/` | batch와 stream이 공유해야 하는 모델/데이터/계약 |
| `stream/` | streaming state, online embedding, assignment, refit, replay |

추가된 것:

- `model/batch/__init__.py`
- `model/common/__init__.py`
- `model/stream/__init__.py`
- `model/IMPLEMENTATION_STATUS.md`
- `python3 -m model.batch.*` 실행 경로
- `python3 -m model.stream.*` 실행 경로

이 단계가 낸 답:

```text
기존 batch는 baseline으로 보존한다.
Streaming은 별도 layer로 붙인다.
둘이 공유해야 하는 것만 common에 둔다.
```

---

## 7. Phase 2: Canonical Batch Extract 추가

기존에는 `batch/extract.py`가 `outputs/embeddings.npz`만 만들었다. 이 산출물은 legacy overlap-window embedding이며 현재 main에서는 재생성 entrypoint가 제거됐다.

Phase 2는 새 entrypoint를 추가했다.

```text
python3 -m model.batch.extract_canonical
  -> outputs/canonical_embeddings.npz
```

당시 기존 `extract.py`를 수정하지 않은 이유:

- 기존 batch clustering baseline을 깨지 않는다.
- overlap-window 산출물과 canonical 산출물을 구분한다.
- streaming/replay 계약용 embedding을 명확히 새 artifact로 둔다.

현재 상태:

- `batch/cluster.py` 기본 입력은 `outputs/canonical_embeddings.npz`다.
- `outputs/embeddings.npz`는 historical evidence로만 남긴다.

추가된 것:

- `model/common/canonical.py`
- `model/batch/extract_canonical.py`
- `outputs/canonical_embeddings.npz`
- `embedding_contract = canonical_event_v1`
- uniqueness validation
- unknown item skip metric
- `history_len`, `context_start_idx`, `rated_at_ts`, `rated_at_iso`

이 단계가 낸 답:

```text
Streaming을 붙이기 전에 batch 쪽에서도 event-centric embedding을 만들 수 있어야 한다.
그래야 online embedding과 batch canonical embedding을 직접 비교할 수 있다.
```

---

## 8. Phase 3: Online User State와 Active Embedding

기존 batch에서는 user sequence가 실행 중 메모리 안에서 만들어졌다. 실행이 끝나면 raw event와 positive projection의 관계가 persistent state로 남지 않았다.

Streaming에서는 이 방식이 부족하다.

- 새 rating event를 계속 저장해야 한다.
- cold-start가 끝나면 positive 판단이 바뀔 수 있다.
- late event가 들어오면 timeline 정렬을 다시 해야 한다.
- unknown item도 raw에는 보존해야 한다.

Phase 3는 `model/stream/state.py`를 추가해 user별 persistent state를 만들었다.

```text
RawRatingEvent
  들어온 rating event를 그대로 저장한다.

PositiveEvent
  현재까지 관측된 user history 기준 positive로 판단한 derived event다.

Active embedding row
  PositiveEvent 중 checkpoint vocabulary에 있는 item만 embedding으로 만든다.
```

상태 파일:

```text
outputs/stream/user_states/{user_id}.json
```

online embedding artifact:

```text
outputs/stream/online_embeddings.npz
```

Phase 3 v1은 incremental suffix update가 아니라 user state 전체 재계산을 택했다.

```text
새 event ingest
  -> raw events 정렬
  -> rating mean/std 재계산
  -> positive projection 재생성
  -> active positive sequence 전체 canonical embedding 재생성
```

이 선택의 의도는 correctness다. Incremental update는 빠르지만 z-score projection이 바뀌는 순간 이전 event의 positive 여부와 `eventIdx`가 바뀔 수 있다. v1에서는 deterministic full recompute가 더 안전하다.

`rawEventId`와 `eventIdx`의 구분도 중요하다.

| id | 의미 |
|---|---|
| `rawEventId` | user별 stable event id. duplicate 방지와 외부 추적에 사용 |
| `eventIdx` | 현재 positive projection 안의 derived index. 재계산 후 바뀔 수 있음 |

추가된 것:

- `model/stream/state.py`
- `model/stream/extract_online.py`
- `outputs/stream/user_states/{user_id}.json`
- `outputs/stream/online_embeddings.npz`
- `outputs/stream/online_embedding_events.jsonl`
- `PositivePolicy`
- `RawRatingEvent`
- `PositiveEvent`
- `compare_with_canonical()`
- online output validation

Phase 3 smoke:

```text
userId: 28
rawEventCount: 2835
positiveEventCount: 1579
activeEventCount: 1579
droppedEventCount: 1256
skippedUnknownItems: 0
online embeddings: (1579, 128)
NaN rows: 0
canonical matched rows: 1579
canonical allclose rows: 1579
max_abs_diff: 0.0
```

이 단계가 낸 답:

```text
Raw event는 정본으로 보존한다.
Positive projection과 active embedding은 state에서 재계산 가능한 view로 둔다.
```

---

## 9. Phase 4: Online Interest Assignment

기존 batch에서는 clustering 결과가 곧 user interest snapshot이었다.

```text
outputs/embeddings.npz
  -> batch/cluster.py
  -> outputs/user_interests.npz
```

Streaming에서는 새 embedding이 들어올 때마다 UMAP/HDBSCAN을 돌릴 수 없다. 따라서 새 event 처리 경로와 refit 경로를 분리했다.

Phase 4의 hot path는 cosine nearest-interest assignment다.

```text
active embedding e_t
  -> existing interest vectors [u_0, u_1, ...]
  -> cosine similarity
  -> nearest interest
  -> threshold 이상이면 assigned
  -> threshold 미만이면 outlier/pending
```

interest state가 없으면 assign하지 않고 pending으로 쌓는다. pending이 충분히 쌓이면 refit request를 연다.

추가된 것:

- `model/stream/interest_assign.py`
- `Interest`
- `InterestState`
- `outputs/stream/interest_states/{user_id}.json`
- `outputs/stream/interest_assignments.jsonl`
- `outputs/stream/refit_requests.jsonl`
- pending buffer
- processed raw event id set
- refit trigger state

Assignment status:

| status | 의미 |
|---|---|
| `assigned` | nearest interest similarity가 threshold 이상 |
| `already_processed` | 같은 `rawEventId`가 이미 처리됨 |
| `pending_no_interest` | interest가 없어 pending |
| `pending_refit_required` | pending count가 refit threshold 도달 |
| `outlier` | similarity가 낮거나 invalid |

Refit trigger reason:

| reason | 발생 조건 |
|---|---|
| `no_interest_pending_events` | interest가 없고 pending count가 충분함 |
| `pending_events` | interest는 있지만 pending count가 충분함 |
| `assigned_since_last_refit` | 마지막 refit 이후 assigned count가 임계 도달 |
| `outlier_since_last_refit` | outlier count가 임계 도달 |

이 단계가 낸 답:

```text
Streaming hot path에서는 clustering하지 않는다.
새 embedding은 cosine assignment만 수행한다.
Refit 필요성은 JSONL request로 분리한다.
```

Phase 4 smoke:

```text
no-interest user 28:
  inputRows: 1579
  pendingAfter: 1579
  refitRequired: true
  refitReasons: no_interest_pending_events
  refit requests: 1

seeded-interest user 28:
  inputRows: 1579
  assigned: 1579
  refitRequired: false

outlier fixture:
  inputRows: 1
  status: outlier
  refitReasons: outlier_since_last_refit
```

---

## 10. Phase 4-1: Triggered Cluster Refit

Phase 4는 request만 연다. 실제 clustering/refit은 Phase 4-1에서 수행한다.

```text
outputs/stream/refit_requests.jsonl
  -> model.stream.cluster_refit
  -> outputs/stream/interest_states/{user_id}.json
  -> outputs/stream/refit_events.jsonl
```

기존 batch clustering과 비교하면 다음과 같다.

| 항목 | 기존 batch clustering | streaming refit |
|---|---|---|
| 입력 | `outputs/embeddings.npz` | `outputs/stream/online_embeddings.npz` |
| 대상 | 전체 또는 selected users | open refit request users |
| 실행 | 수동 batch | request consumer |
| 결과 | `outputs/user_interests.npz` | `interest_states/{user_id}.json` |
| backend | CPU | `auto|gpu|cpu` |

Phase 4-1도 incremental clustering을 하지 않는다. request user의 active online embeddings 전체를 다시 clustering한다.

Refit 후 state update:

```text
interests = 새 cluster mean vectors
pendingRawEventIds = []
processedRawEventIds = active raw event ids 전체
assignedSinceLastRefit = 0
outlierSinceLastRefit = 0
refitRequired = false
refitRequestOpen = false
refitReasons = []
```

Noise label `-1`은 interest vector에서 제외한다. 모든 point가 noise이거나 sample이 부족하면 전체 embedding mean vector 하나를 fallback interest로 만든다.

GPU-first backend도 여기서 추가됐다.

```text
--cluster-backend auto
  -> cuML import 가능: GPU UMAP/HDBSCAN
  -> 불가능: CPU umap-learn + hdbscan fallback
```

최종 GPU dependency 결정:

```text
torch==2.5.1+cu121
RAPIDS/cuML 25.10.0
cuda-toolkit==12.1.1
cupy-cuda12x==13.6.0
scikit-learn==1.7.2
```

이전 `cuml-cu12==26.4.0`은 CUDA 12.9 runtime wheel을 끌고 와서 로컬 driver/PyTorch cu121 조합과 맞지 않았고, `CUDA_ERROR_INVALID_IMAGE`로 실패했다. 이 판단은 `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`에 기록됐다.

이 단계가 낸 답:

```text
기존 UMAP/HDBSCAN multi-interest 구조는 유지한다.
다만 실행 시점을 trigger 기반으로 바꾸고,
가능하면 GPU로 빠르게 닫는다.
```

Phase 4-1 smoke:

```text
CPU fallback:
  user 28 active rows: 1579
  interest count: 21
  noise rows: 43
  request closed

GPU auto:
  user 28 active rows: 1579
  backend: gpu
  interest count: 33
  noise rows: 124
  elapsed: about 0.26 sec
  request closed
```

---

## 11. Phase 5: Replay Engine

기존 batch에는 "시간 순 event stream"을 흘려보내는 실행 단위가 없었다. Phase 5는 ML-32M historical ratings를 streaming event처럼 replay하기 위해 추가됐다.

구조는 두 층이다.

```text
C++ generator
  data/ratings_drop_processed.jsonl
    -> outputs/post/replay_demo/replay_input_events.jsonl

Python orchestrator
  replay_input_events.jsonl
    -> --speed N trace clock
    -> ingress_events.jsonl
    -> extract_online
    -> interest_assign
    -> cluster_refit
    -> replay_events.jsonl
    -> replay_summary.json
```

### 11-1. C++ `rating_replay`

`replay/cpp/rating_replay.cpp`는 user별 JSONL history를 event 단위로 펼치고 global timestamp order로 정렬한다.

정렬 기준:

```text
ratedAtTs
userId
movieId
eventId
```

출력 schema는 `stream_replay_event.v1`이다.

```json
{
  "version": "stream_replay_event.v1",
  "eventId": 0,
  "replayOrder": 0,
  "userId": 28,
  "movieId": 296,
  "rating": 4.0,
  "ratedAt": "2001-01-01T00:00:00Z",
  "ratedAtTs": 978307200.0,
  "source": "ratings_drop_processed"
}
```

### 11-2. Python `replay_pipeline`

`model/stream/replay_pipeline.py`는 `model/stream/trace_replay.py` entrypoint로 동작한다. replay input의 `ratedAtTs`를 기준으로 event별 scheduled wall-clock time을 계산하고, 각 event마다 Phase 3~4-1 CLI를 subprocess로 호출한다.

```text
for each replay event:
  scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / speed
  due time까지 sleep하거나 behind schedule 기록
  ingress_events.jsonl에 emit record append
  extract_online 실행
  interest_assign 실행
  새 open request user별 cluster_refit 실행
  replay_events.jsonl에 trace_event progress/lag append

end:
  replay_summary.json 작성
  experiment metrics 기록
```

Subprocess 체인을 택한 이유:

- 각 phase가 독립 CLI로 테스트 가능하다.
- replay는 phase 내부 구현을 몰라도 된다.
- failure와 log가 phase별로 분리된다.
- 나중에 daemon/message queue로 바꿀 때 경계가 유지된다.

추가된 artifact:

```text
outputs/post/replay_demo/replay_input_events.jsonl
outputs/post/replay_demo/ingress_events.jsonl
outputs/post/replay_demo/replay_events.jsonl
outputs/post/replay_demo/replay_summary.json
outputs/post/replay_demo/user_states/{user_id}.json
outputs/post/replay_demo/online_embeddings.npz
outputs/post/replay_demo/interest_assignments.jsonl
outputs/post/replay_demo/refit_requests.jsonl
outputs/post/replay_demo/refit_events.jsonl
outputs/post/replay_demo/interest_states/{user_id}.json
```

중요한 점은 replay artifact가 `outputs/post/replay_demo/` 아래에 격리된다는 것이다. 기본 `outputs/stream/*`를 덮어쓰지 않는다.

이 단계가 낸 답:

```text
실제 streaming infra 없이도
file-based timestamp trace + event-level subprocess chain으로
closed-loop streaming behavior를 검증한다.
```

Trace replay smoke:

```text
input events: 5
processed events: 5
unique users: 1
speed: 100
trace/scheduled span: 32 sec / 0.32 sec
target/actual throughput: 15.625 EPS / about 0.158 EPS
refit requests opened/closed/skipped: 1 / 0 / 0 with --skip-refit
behind schedule events: 4
elapsed: about 31.74 sec
```

---

## 12. Phase 6: Replay Dashboard

기존 dashboard는 cluster result table을 탐색하는 용도였다. Phase 6은 replay artifact를 읽는 Replay monitor를 추가했다.

중요한 원칙:

```text
Dashboard는 replay pipeline을 실행하지 않는다.
Dashboard는 replay state를 수정하지 않는다.
Dashboard는 Phase 5 artifact를 읽기만 한다.
```

기본 entrypoint:

```text
outputs/post/replay_demo/replay_summary.json
```

읽는 파일:

```text
replay_summary.json
ingress_events.jsonl
replay_events.jsonl
interest_assignments.jsonl
refit_requests.jsonl
refit_events.jsonl
interest_states/{user_id}.json
```

추가된 view:

- run status
- processed/input events
- unique users
- elapsed/throughput
- speed, target/actual throughput
- trace event timeline
- injector/processing/end-to-end latency
- active embedding rows
- assignment status counts
- open/closed/skipped refit events
- user별 interest state browser

이 단계가 낸 답:

```text
Phase 6은 Phase 5 내부 구현을 호출하지 않는다.
파일 기반 contract만 읽는 read-only consumer다.
```

---

## 13. Phase 5/6 파일 기반 계약

`docs/streaming-replay-dashboard-contract.md`는 Phase 5 writer와 Phase 6 reader 사이의 interface contract다.

핵심 원칙:

```text
Phase 5 owns writes under:
  outputs/post/replay_demo/

Phase 6 reads:
  outputs/post/replay_demo/

Phase 6 must not depend on:
  Phase 5 internal functions
  process model
  CLI implementation
  C++ code structure
```

Schema versions:

| artifact | version |
|---|---|
| replay input event | `stream_replay_event.v1` |
| ingress event | `stream_ingress_event.v1` |
| replay progress event | `stream_trace_replay_progress.v1` |
| replay summary | `stream_trace_replay_summary.v1` |

이 계약이 중요한 이유는 현재 구현이 파일 기반이어도 producer/consumer 역할이 분리되기 때문이다.

```text
현재:
  JSONL/NPZ 파일이 queue/topic 역할

나중:
  message queue나 service로 바꿔도 phase boundary 유지 가능
```

---

## 14. End-to-End 실행이 의미하는 것

Trace replay smoke command 하나가 다음을 모두 통과한다.

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --reset-output \
  --generate-events \
  --replay-user-id 28 \
  --limit-events 5 \
  --speed 100 \
  --refit-min-events 3 \
  --assign-trigger-count 3 \
  --outlier-trigger-count 3 \
  --min-cluster-size 2 \
  --cluster-dim 3 \
  --cluster-backend cpu \
  --skip-refit \
  --run-id trace_replay_smoke
```

이 명령이 닫는 흐름:

```text
C++ event generation
  -> timestamp-sorted replay input
  -> trace-clock event injection
  -> ingress schedule/lag log
  -> online user state update
  -> SASRec canonical embedding
  -> cosine interest assignment
  -> refit request open
  -> optional UMAP/HDBSCAN refit
  -> optional interest state replace
  -> replay progress log
  -> replay summary entrypoint
  -> dashboard read
```

즉 end-to-end streaming pipeline이란 여기서 다음 artifact chain이 완성됐다는 뜻이다.

```text
event
  -> user state
  -> active embedding
  -> assignment
  -> refit request
  -> interest state
  -> replay summary/dashboard
```

---

## 15. 기존 Batch 대비 변경 요약

| 영역 | 기존 batch | streaming 브랜치 |
|---|---|---|
| 모델 학습 | 전체 데이터 batch 학습 | 유지 |
| checkpoint | batch extract/cluster에서 사용 | stream inference에서도 재사용 |
| positive policy | 전체 history 기준 `z > 0` | raw state 기준 event ingest 후 재계산 |
| hidden state | overlap-window context별 hidden state | canonical event embedding |
| event uniqueness | 보장 안 됨 | `(user_id, event_idx)` unique |
| raw event state | 없음 | `user_states/{user_id}.json` |
| online embedding | 없음 | `online_embeddings.npz` |
| interest state | `user_interests.npz` snapshot | `interest_states/{user_id}.json` |
| assignment | cluster 결과에 포함 | cosine nearest-interest online step |
| refit trigger | 없음 | pending/outlier/assigned count 기반 |
| refit backend | CPU UMAP/HDBSCAN | GPU-first cuML, CPU fallback |
| replay | 없음 | C++ generator + Python orchestrator |
| recommendation | batch/offline 후보 | `stream/recommend_online.py`, `replay_pipeline --recommend` |
| dashboard | cluster explorer | replay monitor + recommendation view read-only 추가 |
| phase boundary | script 중심 | file/JSONL/NPZ contract 중심 |

---

## 16. 이 브랜치가 의도적으로 하지 않은 것

Streaming end-to-end가 됐다고 해서 모든 추천 serving 기능이 완성된 것은 아니다.

명시적으로 하지 않은 것:

| 항목 | 상태 |
|---|---|
| full online training | deferred |
| 매 event UMAP/HDBSCAN | 하지 않음 |
| `u_k` 기반 recommendation scoring | `stream/recommend_online.py`와 `replay_pipeline --recommend` 구현됨 |
| downstream Recall/NDCG 평가 | 아직 없음 |
| dashboard-driven state mutation | 하지 않음 |
| legacy batch 산출물 제거 | 하지 않음 |

이 브랜치는 먼저 streaming state/refit/replay 계약을 닫았고, 이후 merge에서 `u_k` 기반 recommendation scoring이 추가됐다. 아직 남은 것은 추천 품질 평가와 batch recommendation 입력 계약 정리다.

---

## 17. 최종 해석

이 브랜치는 기존 batch 추천 시스템을 다음 방식으로 streaming화했다.

```text
기존 batch:
  정적 user history
  -> SASRec 학습
  -> overlap hidden state extract
  -> batch clustering
  -> static interest snapshot

streaming 브랜치:
  rating event stream
  -> persistent raw user state
  -> positive projection 재계산
  -> canonical event embedding
  -> online cosine assignment
  -> trigger-based refit request
  -> GPU-first cluster refit
  -> replay artifact/dashboard
```

가장 중요한 변화는 hidden state의 의미를 바꾼 것이다.

```text
기존:
  window-context dependent hidden state

변경:
  event-centric canonical hidden state
```

그리고 가장 중요한 구조적 변화는 assignment와 refit을 분리한 것이다.

```text
hot path:
  event ingest
  state update
  embedding inference
  cosine assignment

heavy path:
  refit request
  UMAP/HDBSCAN
  interest state replace
```

결론:

```text
streaming training은 이번 범위에서 deferred다.
전환의 본질은 batch-trained representation을
event-level canonical contract와 online state/refit pipeline으로 감싼 것이다.
```

이 브랜치는 기존 batch의 모델 자산을 유지하면서, 서비스형 event stream에 필요한 state transition과 observability를 새로 만든 브랜치다.
