# Current Pipeline Snapshot

이 문서는 현재 batch / streaming 최종 구현을 기준으로 "무엇이 어떤 순서로 돌고, 어떤 데이터가 흐르며, 어디가 다음 수정 후보인가"를 정리한다. 실행 인계 문서는 `docs/streaming-e2e-pipeline.md`, 전체 데이터 흐름 요약은 `docs/data-flow.md`, artifact 계약은 `docs/artifacts.md`와 `docs/streaming-replay-dashboard-contract.md`를 함께 본다.

주의할 점은 현재 streaming 구현이 production streaming service가 아니라 file-based online/replay pipeline이라는 것이다. 각 단계는 CLI로 실행되고 JSON/JSONL/NPZ artifact를 읽고 쓴다. `model.stream.replay_pipeline`은 이 CLI들을 micro-batch 단위로 호출하는 orchestrator다.

## 1. 한 줄 그림

```text
preprocess
  -> batch train
  -> canonical event embedding
  -> batch cluster/export/dashboard

preprocess
  -> batch train
  -> stream event ingest
  -> online user state
  -> active positive embedding snapshot
  -> interest assignment
  -> refit request
  -> per-user cluster refit
  -> online recommendation
  -> replay summary/dashboard
```

Batch와 streaming은 같은 checkpoint(`outputs/sasrec_cl.pt`)와 item vocabulary(`outputs/item2idx.json`)를 공유한다. Batch는 전체 history를 한 번에 읽어서 canonical event embedding과 user별 cluster를 만든다. Streaming은 rating event를 누적 state에 반영한 뒤, 현재 시점의 active positive event만 다시 embedding으로 만들고 interest state를 점진적으로 갱신한다.

## 2. Batch Pipeline

### 2.1 Train

```bash
python3 -m model.batch.train
```

입력:

- `data/ratings_drop_processed.jsonl`
- `data/movies_processed_drop.csv`

출력:

- `outputs/sasrec_cl.pt`
- `outputs/sasrec_cl_best.pt`
- `outputs/item2idx.json`
- `experiments/model/<run_id>/manifest.json`
- `experiments/model/<run_id>/metrics.jsonl`

현재 모델은 `SASRecCL`이다. 기본 설정은 `seq_len=100`, `d_model=128`, `num_heads=2`, `num_layers=2`, `dropout=0.2`, `cl_lambda=0.1` 계열이다. 학습 단계는 batch와 streaming 양쪽에서 사용할 item embedding과 sequence encoder를 만든다.

### 2.2 Canonical Event Embedding

```bash
python3 -m model.batch.extract_canonical
```

입력:

- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `data/ratings_drop_processed.jsonl`
- `data/movies_processed_drop.csv`

출력:

- `outputs/canonical_embeddings.npz`
- `experiments/model/<run_id>/manifest.json`
- `experiments/model/<run_id>/metrics.jsonl`

`canonical_embeddings.npz`는 positive event 하나당 row 하나를 저장한다.

주요 key:

- `embeddings`: `(N, 128)` hidden state
- `user_ids`: row별 user id
- `event_idx`: user 내부 positive event index
- `movie_ids`: row의 movie id
- `rated_at_ts`, `rated_at_iso`: event time
- `history_len`: 해당 event를 만들 때 사용한 history 길이
- `context_start_idx`: `seq_len` window가 시작된 positive event index

기본 필터는 `min_interactions=1000`, `min_activity_days=30`이다. `--user-id`를 쓰면 단일 user 추출용으로 이 필터를 우회한다. 예전 `model.batch.extract` entrypoint는 현재 경로가 아니며, 현재 공식 추출 경로는 `model.batch.extract_canonical`이다.

### 2.3 Cluster

```bash
python3 -m model.batch.cluster
```

기본 입력:

- `outputs/canonical_embeddings.npz`
- `data/movies_processed_drop.csv`

출력:

- `outputs/user_interests.npz`
- `outputs/batch/interest_states/{user_id}.json`
- `experiments/model/<run_id>/manifest.json`
- `experiments/model/<run_id>/metrics.jsonl`

`model.batch.cluster`는 user별 embedding sequence에 UMAP + HDBSCAN을 수행한다. Cluster backend는 `--cluster-backend auto|gpu|cpu`이며, 공통 구현은 `model.common.cluster`에 있다. `auto`는 RAPIDS/cuML GPU 사용 가능 여부를 먼저 확인하고, 불가능하거나 runtime 실패가 나면 CPU 구현으로 진행한다.

현재 산출물은 두 계층으로 나뉜다.

- `outputs/user_interests.npz`: dashboard/visualize/export용 label, UMAP 좌표, sliding window `K(t)` 데이터
- `outputs/batch/interest_states/{user_id}.json`: streaming `InterestState`와 호환되는 user별 interest vector JSON

중요한 점은 `outputs/user_interests.npz`에 더 이상 `interest_vectors`를 넣지 않는다는 것이다. Interest vector의 정본은 `outputs/batch/interest_states/*.json`이다.

### 2.4 Export / Visualize / Batch Recommend

Dashboard table export:

```bash
python3 -m model.batch.export_clusters \
  --input outputs/user_interests.npz \
  --output data/clustering/user_clusters.parquet
```

Visualize:

```bash
python3 -m model.batch.visualize_clusters
```

Batch recommend:

```bash
python3 -m model.batch.recommend
```

현재 주의할 부분은 batch recommend 계약이다. `model.batch.recommend`는 `--interests` NPZ 안에 `iv_user_ids`, `iv_cluster_ids`, `interest_vectors` key가 있다고 가정한다. 그러나 현재 `model.batch.cluster`는 interest vector를 JSON state directory에 저장하고, `outputs/user_interests.npz`는 시각화/export용 key만 저장한다. 따라서 batch recommendation을 현재 batch cluster 결과에 바로 붙이려면 다음 중 하나가 필요하다.

- `model.batch.recommend`가 `outputs/batch/interest_states/*.json`을 직접 읽도록 수정한다.
- 또는 JSON interest state를 기존 NPZ 계약으로 변환하는 bridge를 추가한다.

## 3. Streaming Pipeline

현재 streaming 흐름은 다음 artifact 흐름으로 이해하면 된다.

```text
rating event JSONL
  -> model.stream.extract_online
  -> user_states/{user_id}.json
  -> online_embeddings.npz
  -> online_embedding_events.jsonl

online_embeddings.npz
  -> model.stream.interest_assign
  -> interest_states/{user_id}.json
  -> interest_assignments.jsonl
  -> refit_requests.jsonl

refit_requests.jsonl + online_embeddings.npz
  -> model.stream.cluster_refit
  -> interest_states/{user_id}.json
  -> refit_events.jsonl

interest_states/{user_id}.json + user_states/{user_id}.json
  -> model.stream.recommend_online
  -> stream_recommendations.jsonl
```

### 3.1 Event Ingest

단일 event 또는 JSONL event를 `extract_online`에 넣는다.

```bash
python3 -m model.stream.extract_online \
  --event-jsonl path/to/events.jsonl
```

Event input은 camelCase와 snake_case를 모두 수용한다.

- `userId` 또는 `user_id`
- `movieId` 또는 `movie_id`
- `rating`
- `ratedAt` 또는 `rated_at`

Replay pipeline은 micro-batch마다 `outputs/stream/replay_demo/batches/batch_000000.jsonl` 같은 파일을 만들고, 그 batch file을 `extract_online --event-jsonl`에 넘긴다.

### 3.2 Online User State

`extract_online`은 user별 state를 읽고 rating event를 append한 뒤 저장한다.

기본 위치:

- standalone: `outputs/stream/user_states/{user_id}.json`
- replay: `outputs/stream/replay_demo/user_states/{user_id}.json`

State 버전:

- `online_user_state.v1`

주요 field:

- `userId`
- `seqLen`
- `nextRawEventId`
- `positivePolicy`
- `rawEvents`
- `positiveEvents`
- `stats`
- `updatedAt`

`rawEvents`는 들어온 rating event 전체를 보존한다. 낮은 rating도 raw state에는 남는다. `rawEventId`는 user state 안에서 안정적인 event id이며, downstream dedupe와 processed tracking은 이 값을 기준으로 한다.

`positiveEvents`는 raw event 전체를 다시 평가해서 만든 현재 시점의 positive projection이다. 기본 policy는 `observed_user_zscore_v1`이다.

- `minRatingsForZscore=3`
- `zThreshold=0.0`
- `optimisticColdStart=True`

현재 policy 해석:

- raw rating 수가 `minRatingsForZscore`보다 작고 optimistic cold start가 켜져 있으면 관측 event를 positive로 둔다.
- rating 표준편차가 0에 가까운 경우에도 optimistic policy면 positive로 둔다.
- 그 외에는 user 내부 z-score가 threshold보다 큰 event만 positive로 둔다.
- `item2idx`에 없는 movie는 `skipped_unknown`으로 표시되고 embedding row에는 들어가지 않는다.

중요한 점은 `eventIdx`가 안정 id가 아니라는 것이다. 새 raw event가 들어오면 z-score 평균/표준편차가 바뀔 수 있고, 기존 event의 positive 여부나 `eventIdx`가 다시 계산될 수 있다. 안정적인 event 추적에는 `rawEventId`를 써야 한다.

### 3.3 Online Embedding Snapshot

`extract_online`은 touched user들의 현재 active positive event를 다시 embedding으로 만든다.

기본 위치:

- standalone: `outputs/stream/online_embeddings.npz`
- replay: `outputs/stream/replay_demo/online_embeddings.npz`

주요 key:

- `embeddings`: active positive event hidden state
- `user_ids`
- `raw_event_ids`
- `event_idx`
- `movie_ids`
- `rated_at_ts`, `rated_at_iso`
- `history_len`
- `context_start_idx`
- `status`

이 파일은 append-only delta가 아니다. 매 실행마다 이번 호출에서 처리한 user들의 현재 active embedding snapshot을 overwrite한다. Replay에서는 batch마다 이 파일이 replay output root 아래에서 갱신된다. 따라서 이 파일의 row 수는 "이번 micro-batch에서 새로 생긴 embedding 수"가 아니라 "이번에 touched된 user들의 현재 active row 수"다.

이 설계는 canonical batch embedding과 비교하기 쉽다는 장점이 있지만, user history가 커질수록 반복 embedding 비용이 커지고, downstream 단계가 snapshot/delta 차이를 반드시 이해해야 한다.

### 3.4 Interest Assignment

```bash
python3 -m model.stream.interest_assign \
  --embeddings outputs/stream/online_embeddings.npz
```

입력:

- `online_embeddings.npz`
- 기존 `interest_states/{user_id}.json`이 있으면 읽음

출력:

- `interest_states/{user_id}.json`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`

State 버전:

- `online_interest_state.v1`

주요 field:

- `interests`
- `pendingRawEventIds`
- `processedRawEventIds`
- `assignedSinceLastRefit`
- `outlierSinceLastRefit`
- `refitRequired`
- `refitRequestOpen`
- `refitReasons`

처리 방식:

1. `online_embeddings.npz` row를 user별로 읽는다.
2. `rawEventId`가 이미 `processedRawEventIds`에 있으면 `already_processed` record를 남긴다.
3. interest state가 없거나 interest가 비어 있으면 row를 pending으로 둔다.
4. interest vector가 있으면 cosine similarity로 가장 가까운 interest를 찾는다.
5. similarity가 threshold 이상이면 assign하고 `processedRawEventIds`에 넣는다.
6. similarity가 낮거나 invalid하면 pending/outlier로 둔다.
7. pending/outlier/assigned count가 trigger를 넘으면 `refit_requests.jsonl`에 open request를 append한다.

기본 replay trigger:

- `similarity_threshold=0.2`
- `refit_min_events=20`
- `assign_trigger_count=50`
- `outlier_trigger_count=10`

`interest_assignments.jsonl`은 audit log에 가깝다. Snapshot 입력 때문에 이전 row가 다시 들어오면 `already_processed`가 반복 기록될 수 있다.

### 3.5 Cluster Refit

```bash
python3 -m model.stream.cluster_refit \
  --embeddings outputs/stream/online_embeddings.npz \
  --refit-requests outputs/stream/refit_requests.jsonl
```

입력:

- `online_embeddings.npz`
- `refit_requests.jsonl`
- 기존 `interest_states/{user_id}.json`
- `data/movies_processed_drop.csv` optional genre label source

출력:

- 갱신된 `interest_states/{user_id}.json`
- `refit_events.jsonl`

처리 방식:

1. `refit_requests.jsonl`에서 `status=open` request를 user별로 읽는다.
2. 대상 user의 active embedding row를 `online_embeddings.npz`에서 가져온다.
3. active row가 `refit_min_events`보다 작으면 `refit_events.jsonl`에 `skipped`를 남긴다.
4. 충분하면 active row 전체를 UMAP + HDBSCAN으로 recluster한다.
5. non-noise cluster가 있으면 cluster centroid를 interest vector로 저장한다.
6. 모든 row가 noise면 전체 평균 vector 하나를 fallback interest로 만든다.
7. state의 `pendingRawEventIds`를 비우고, active row의 `rawEventId` 전체를 `processedRawEventIds`로 둔다.
8. `refitRequired=False`, `refitRequestOpen=False`로 닫고 `refit_events.jsonl`에 `closed`를 남긴다.

현재 refit은 incremental update가 아니다. Trigger가 열리면 해당 user의 active embedding snapshot 전체를 다시 cluster한다.

### 3.6 Online Recommend

```bash
python3 -m model.stream.recommend_online \
  --interest-state-dir outputs/stream/interest_states \
  --user-state-dir outputs/stream/user_states
```

입력:

- `interest_states/{user_id}.json`
- `user_states/{user_id}.json`
- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `data/movies_processed_drop.csv`

출력:

- `outputs/stream/stream_recommendations.jsonl`

처리 방식:

1. interest state JSON에서 interest vector를 읽는다.
2. checkpoint의 `item_emb.weight`를 candidate item vector로 쓴다.
3. user state의 `positiveEvents`를 seen set으로 읽는다.
4. 기본값은 seen positive movie를 추천 후보에서 제외한다.
5. 각 candidate item에 대해 `max_k(u_k^T v_i)`를 score로 삼는다.
6. top-K row를 JSONL에 append한다.

출력 record 주요 field:

- `recordedAt`
- `runId`
- `userId`
- `rank`
- `movieId`
- `itemIdx`
- `title`, `releaseYear`, `genres`, `ratingAvg`, `ratingCount`
- `score`
- `bestClusterId`
- `clusterScores`
- `normalize`
- `includeSeen`
- `topK`

주의할 점은 output이 overwrite가 아니라 append라는 것이다. 같은 run/output path로 재실행하면 recommendation row가 중복될 수 있다.

## 4. Replay Orchestrator

```bash
python3 -m model.stream.replay_pipeline \
  --generate-events \
  --reset-output \
  --micro-batch-size 20 \
  --cluster-backend auto \
  --recommend
```

기본 output root:

- `outputs/stream/replay_demo/`

Replay pipeline은 새 모델 로직을 구현하지 않는다. 이미 있는 streaming CLI를 micro-batch마다 순서대로 호출한다.

Micro-batch 처리 순서:

1. `batches/batch_000000.jsonl` 생성
2. `model.stream.extract_online` 호출
3. `online_embeddings.npz` row 수 확인
4. `model.stream.interest_assign` 호출
5. 새로 append된 `refit_requests.jsonl` request를 읽음
6. request user별로 `model.stream.cluster_refit` 호출
7. `--recommend`가 있으면 batch user별로 `model.stream.recommend_online` 호출
8. `replay_events.jsonl`에 micro-batch progress record append
9. 전체 완료 시 `replay_summary.json` 저장

주요 replay artifact:

- `replay_input_events.jsonl`
- `replay_events.jsonl`
- `replay_summary.json`
- `batches/*.jsonl`
- `user_states/{user_id}.json`
- `online_embeddings.npz`
- `online_embedding_events.jsonl`
- `interest_states/{user_id}.json`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`
- `refit_events.jsonl`
- `stream_recommendations.jsonl`

`replay_summary.json`의 `totals.activeEmbeddingRows`는 micro-batch별 active snapshot row 수를 합산한 값이다. 최종 active embedding row 수가 아니다.

## 5. Dashboard Connection

Batch dashboard branch:

```text
outputs/user_interests.npz
  -> model.batch.export_clusters
  -> data/clustering/user_clusters.parquet
  -> dashboard/cluster_dashboard.py Cluster explorer
```

Replay monitor branch:

```text
outputs/stream/replay_demo/replay_summary.json
  -> summary.paths.*
  -> dashboard/cluster_dashboard.py Replay monitor
```

Dashboard는 replay/stream state를 만들거나 수정하지 않는다. Replay monitor는 `replay_summary.json`의 `paths` 값을 stable entrypoint로 삼고, 있으면 해당 path를 우선 사용한다.

## 6. 현재 문제 포인트

### P0. Batch recommendation 계약 불일치

`model.batch.recommend`는 interest vector NPZ를 기대하지만, 현재 batch cluster의 interest vector 정본은 `outputs/batch/interest_states/*.json`이다. Batch 추천을 다시 실험하려면 이 bridge를 먼저 정리해야 한다.

수정 후보:

- `model.batch.recommend --interest-state-dir outputs/batch/interest_states` 지원
- 또는 `model.batch.export_interest_vectors` 같은 변환 entrypoint 추가

### P0. Refit request lifecycle이 append-only log에 의존

`cluster_refit`은 `refit_requests.jsonl`에서 `status=open`인 request를 읽지만, request log 자체에 closed record를 쓰지는 않는다. State와 `refit_events.jsonl`에는 닫힘이 남지만, standalone으로 `cluster_refit`을 다시 돌릴 때 오래된 open request를 다시 볼 수 있다.

Replay pipeline은 "이번 micro-batch 이후 새로 append된 request"만 처리해서 이 문제를 일부 피한다. 하지만 standalone 운용 기준으로는 request open/closed lifecycle을 더 명확히 해야 한다.

수정 후보:

- `refit_requests.jsonl`에 `closed` 또는 `superseded` event를 append
- `cluster_refit`이 interest state의 `refitRequestOpen`도 함께 확인
- request id를 도입해서 request와 close event를 연결

### P0. Refit skipped 상태 처리

`cluster_refit`이 active row 부족으로 `skipped`를 남기는 경우, state/request 상태가 계속 open으로 남을 수 있다. 그러면 반복 skip이나 새 request block이 발생할 수 있다.

수정 후보:

- skipped reason별로 request를 유지할지 닫을지 정책 결정
- insufficient case에서 `refitRequestOpen=False`로 닫고 다음 assign에서 다시 열게 할지 검토
- `retryAfterRawEventCount` 같은 조건부 재시도 정보 추가

### P1. `online_embeddings.npz`가 delta가 아니라 snapshot

현재 downstream은 매번 snapshot을 받는다. 그래서 이미 처리된 row가 `interest_assign`에 다시 들어오고, `already_processed` audit record가 누적될 수 있다. 또한 replay summary의 active row 합산값은 직관적인 "처리된 새 embedding 수"가 아니다.

수정 후보:

- snapshot file과 delta file을 분리
- `extract_online`이 `new_raw_event_ids` 또는 `changed_raw_event_ids` metadata를 같이 기록
- replay summary에 `activeSnapshotRows`와 `newEmbeddingRows`를 분리

### P1. Positive projection이 과거 event를 재분류할 수 있음

새 rating event가 들어오면 user 평균/표준편차가 바뀌고, 이전 event의 positive 여부가 바뀔 수 있다. 이 때문에 `eventIdx`는 재계산될 수 있고 downstream 안정 key가 될 수 없다.

수정 후보:

- 문서와 코드에서 안정 key는 항상 `rawEventId`라고 강제
- projection 변경으로 빠진 event를 downstream state에서 어떻게 처리할지 정책화
- audit log에 `projection_changed` summary 추가

### P1. Online recommendation output이 append-only

`stream_recommendations.jsonl`은 append 방식이다. 동일 output root에서 재실행하면 같은 user/run의 top-K가 중복될 수 있다.

수정 후보:

- replay run은 `--reset-output` 사용을 기본 운용 규칙으로 유지
- recommendation record에 `batchId` 또는 `replayEventEnd`를 추가
- dashboard는 최신 `recordedAt` 또는 batch 기준으로 dedupe

### P1. Streaming은 service가 아니라 subprocess/file orchestration

현재 구조는 검증과 replay에는 적합하지만, production online serving 형태는 아니다. Concurrency, lock, multi-writer, exactly-once 처리는 아직 없다.

수정 후보:

- state write lock 또는 atomic write 정책 점검
- event ingestion idempotency key 도입
- replay pipeline과 실제 serving adapter를 분리

### P2. Config가 CLI option에 흩어져 있음

Streaming threshold, refit trigger, cluster backend, recommend option이 여러 CLI argument로 흩어져 있다.

수정 후보:

- `configs/streaming/*.yaml` 같은 run config 도입
- manifest에 resolved config를 더 명확히 기록
- dashboard/replay summary에 핵심 threshold 노출

### P2. Artifact 최신성과 재현성

현재 문서는 code contract 기준이다. `outputs/` 아래 실제 artifact는 최근 smoke/demo 실행 결과일 수 있고 full rerun 결과가 아닐 수 있다.

수정 후보:

- full batch rerun, full canonical extraction, representative replay를 새 run id로 재생성
- `experiments/model/<run_id>/notes.md`에 artifact freshness 기록
- dashboard 기본 path가 demo artifact인지 production-scale artifact인지 명확히 표시

## 7. 바로 이어질 작업 제안

1. Batch recommendation bridge를 먼저 정리한다. 현재 batch cluster 결과로 추천까지 이어지는 길이 끊겨 있으므로, `model.batch.recommend`가 JSON interest state를 읽게 하는 것이 가장 직접적이다.
2. Refit request lifecycle을 정리한다. `open -> closed/skipped/superseded`가 request log와 state 양쪽에서 일관되게 보이게 만든다.
3. Streaming embedding snapshot/delta 의미를 분리한다. 최소한 replay summary와 assignment log에서 snapshot으로 인한 재처리 record를 구분한다.
4. Representative replay를 `--reset-output --recommend`로 다시 실행하고 dashboard가 읽는 artifact를 기준 run으로 고정한다.
5. 이후 실제 추천 품질 평가를 붙인다. Batch/stream 추천 둘 다 `seen` 제외, top-K, score normalization 정책을 같은 기준으로 비교해야 한다.
