# Current Pipeline Snapshot

이 문서는 현재 batch / streaming 최종 구현을 기준으로 "무엇이 어떤 순서로 돌고, 어떤 데이터가 흐르며, 어디가 다음 수정 후보인가"를 정리한다. 실행 인계 문서는 `docs/streaming-e2e-pipeline.md`, 전체 데이터 흐름 요약은 `docs/data-flow.md`, artifact 계약은 `docs/artifacts.md`와 `docs/streaming-replay-dashboard-contract.md`를 함께 본다.

주의할 점은 현재 streaming 구현이 production streaming service가 아니라 file-based online/trace replay pipeline이라는 것이다. 각 단계는 CLI로 실행되고 JSON/JSONL/NPZ artifact를 읽고 쓴다. `model.stream.replay_pipeline`은 timestamp trace를 `--speed N` virtual clock 기준으로 event 단위 주입하고 lag/throughput metric을 기록한다.

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

Temporal streaming run에서는 기본 루트 산출물 대신 `outputs/pre/<run_label>/` 아래의 checkpoint/item2idx/canonical/user state/interest state를 명시적으로 넘긴다. 예: 2022 E2E는 `T=2022-01-01T00:00:00Z`, pre-T는 `ratedAt < T`, post-T replay는 `ratedAt >= T`를 사용한다.

## Current Verification Boundary

현재 GitHub-visible 문서는 code contract와 대표 smoke 기준이다. `e2e_streaming_smoke_auto_fallback`은 CUDA가 보이지만 런타임/드라이버 조합이 맞지 않는 환경에서 `--cluster-backend auto`가 CPU fallback으로 replay를 완료하는 기준 run이다: user 28, 30/30 events, 2 micro-batches, refit opened/closed/skipped 2/2/0, elapsed 약 32.4초.

Temporal 2022 runbook은 cutoff와 artifact 경로 계약을 정리한 상태다. pre-T train, canonical extraction, cluster, seed state, post-T seeded replay를 한 번에 잇는 full temporal verification은 아직 별도 대표 run으로 남겨야 한다. 기존 `outputs/pre/temporal_2022/`와 `outputs/post/temporal_2022_*` artifact는 최신 full 검증 결과로 가정하지 않는다.

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
- local `experiments/model/<run_id>/manifest.json`
- local `experiments/model/<run_id>/metrics.jsonl`

현재 모델은 `SASRecCL`이다. 기본 설정은 `seq_len=100`, `d_model=128`, `num_heads=2`, `num_layers=2`, `dropout=0.2`, `cl_lambda=0.1` 계열이다. 학습 단계는 batch와 streaming 양쪽에서 사용할 item embedding과 sequence encoder를 만든다.

Temporal cutoff 학습은 `--max-rated-at-exclusive <T>`로 T 이전 rating만 사용한다. 모델 산출물은 `--output-dir outputs/pre/<run_label>`로 분리할 수 있으며, 기존 기본값은 `outputs/` 루트다.

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
- local `experiments/model/<run_id>/manifest.json`
- local `experiments/model/<run_id>/metrics.jsonl`

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

Temporal cutoff 추출은 `--max-rated-at-exclusive <T>`와 pre-T checkpoint/item2idx를 함께 지정한다. 예: `--checkpoint outputs/pre/temporal_2022/sasrec_cl.pt --item2idx outputs/pre/temporal_2022/item2idx.json --output outputs/pre/temporal_2022/canonical_embeddings.npz`.

### 2.3 Cluster

```bash
python3 -m model.batch.cluster
```

기본 입력:

- `outputs/canonical_embeddings.npz`
- `data/movies_processed_drop.csv`

출력:

- `outputs/user_interests.npz`
- `outputs/batch/state.sqlite`
- local `experiments/model/<run_id>/manifest.json`
- local `experiments/model/<run_id>/metrics.jsonl`

`model.batch.cluster`는 user별 embedding sequence에 UMAP + HDBSCAN을 수행한다. Cluster backend는 `--cluster-backend auto|gpu|cpu`이며, 공통 구현은 `model.common.cluster`에 있다. `auto`는 RAPIDS/cuML GPU 사용 가능 여부를 먼저 확인하고, 불가능하거나 runtime 실패가 나면 CPU 구현으로 진행한다.

`--output`으로 visualization/export NPZ 경로를 지정할 수 있다. Temporal 2022 run은 `--output outputs/pre/temporal_2022/user_interests.npz --state-db outputs/pre/temporal_2022/state.sqlite`처럼 pre-T 모델/state 산출물을 `outputs/pre/` 아래에 둔다.

현재 산출물은 두 계층으로 나뉜다.

- `outputs/user_interests.npz`: dashboard/visualize/export용 label, UMAP 좌표, sliding window `K(t)` 데이터
- `outputs/batch/state.sqlite`: streaming `InterestState`와 호환되는 user별 interest vector payload

중요한 점은 `outputs/user_interests.npz`에 더 이상 `interest_vectors`를 넣지 않는다는 것이다. Interest vector의 정본은 SQLite `interest_states`/`interest_vectors` 테이블이다.

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

- `model.batch.recommend`가 SQLite interest state를 직접 읽도록 수정한다.
- 또는 SQLite interest state를 기존 NPZ 계약으로 변환하는 bridge를 추가한다.

## 3. Streaming Pipeline

현재 streaming 흐름은 다음 artifact 흐름으로 이해하면 된다.

```text
rating event JSONL
  -> model.stream.extract_online
  -> SQLite user state
  -> online_embeddings.npz
  -> online_embedding_events.jsonl

online_embeddings.npz
  -> model.stream.interest_assign
  -> SQLite interest state
  -> interest_assignments.jsonl
  -> refit_requests.jsonl

refit_requests.jsonl + online_embeddings.npz
  -> model.stream.cluster_refit
  -> SQLite interest state
  -> refit_events.jsonl

SQLite interest/user state
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

Trace replay pipeline은 `replay_input_events.jsonl`의 `ratedAtTs`를 기준으로 event별 schedule을 계산하고, `extract_online --event-json`에 단일 event payload를 넘긴다. 주입 시각과 lag는 `outputs/stream/replay_demo/ingress_events.jsonl`에 append된다.

### 3.1.1 Pre-T User State Seed

```bash
python3 -m model.stream.seed_pre_t_state \
  --max-rated-at-exclusive 2022-01-01T00:00:00Z \
  --item2idx outputs/pre/temporal_2022/item2idx.json \
  --state-db outputs/pre/temporal_2022/state.sqlite
```

`seed_pre_t_state`는 `ratings_drop_processed.jsonl`에서 cutoff 이전 rating만 읽어 replay 시작용 SQLite user state를 만든다. 같은 seed DB에 pre-T batch cluster가 만든 interest state가 있으면 해당 user의 pre-T active `rawEventId`를 `processedRawEventIds`에 표시해, post-T replay 첫 이벤트에서 과거 active row가 신규 assignment/refit 대상으로 처리되지 않게 한다.

### 3.2 Online User State

`extract_online`은 user별 state를 읽고 rating event를 append한 뒤 저장한다.

기본 위치는 `--runtime-db`/`--state-db` SQLite store다. `--state-dir`를 명시하면 legacy/debug용 per-user JSON도 사용할 수 있다.

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

이 파일은 append-only delta가 아니다. 매 실행마다 이번 호출에서 처리한 user들의 현재 active embedding snapshot을 overwrite한다. Replay에서는 event마다 이 파일이 replay output root 아래에서 갱신된다. 따라서 이 파일의 row 수는 "이번 event에서 새로 생긴 embedding 수"가 아니라 "이번에 touched된 user들의 현재 active row 수"다.

이 설계는 canonical batch embedding과 비교하기 쉽다는 장점이 있지만, user history가 커질수록 반복 embedding 비용이 커지고, downstream 단계가 snapshot/delta 차이를 반드시 이해해야 한다.

### 3.4 Interest Assignment

```bash
python3 -m model.stream.interest_assign \
  --embeddings outputs/stream/online_embeddings.npz
```

입력:

- `online_embeddings.npz`
- 기존 SQLite interest state가 있으면 읽음

출력:

- SQLite interest state
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
- 기존 SQLite interest state
- `data/movies_processed_drop.csv` optional genre label source

출력:

- 갱신된 SQLite interest state
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
  --state-db outputs/stream/state.sqlite
```

입력:

- SQLite interest state
- SQLite user state
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
  --speed 100 \
  --cluster-backend auto \
  --recommend
```

기본 output root:

- `outputs/stream/replay_demo/`

Replay pipeline은 새 모델 로직을 구현하지 않는다. replay input의 `ratedAtTs`를 trace clock으로 삼고, `scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / speed` 기준으로 event를 emit한 뒤 기존 streaming CLI를 event 단위로 호출한다.

Temporal seeded replay는 `--start-rated-at <T>`로 post-T input을 만들고, `--seed-state-db --seed-run-id`로 pre-T state를 lazy-load한다. pre state는 replay output root로 복사하지 않는다. 예: `--output-root outputs/post/temporal_2022_events_1000 --seed-state-db outputs/pre/temporal_2022/state.sqlite --seed-run-id temporal_2022`.

`outputs/stream/replay_demo/replay.sqlite`는 runtime state/control-plane 정본이다. Payload, state summary, assignment, refit lifecycle, stage latency, recommendation metadata, embedding snapshot row index를 SQLite에 기록한다. Checkpoint, `online_embeddings.npz`, canonical/batch embedding matrix 같은 대형 vector artifact는 파일 정본으로 유지하고 SQLite에는 metadata/index만 둔다.

`python3 -m model.stream.runtime_report --db outputs/stream/replay_demo/replay.sqlite`는 이 DB를 읽어 dominant stage latency, event lag, refit lifecycle, repeated processing signal, user state progress를 markdown/JSON으로 요약한다. 이는 dashboard 입력 정본은 아니고, replay 후 문제 포인트를 빠르게 찾기 위한 read-only 분석 도구다.

Event 처리 순서:

1. `replay_input_events.jsonl`의 다음 event에 대해 `scheduledAt` 계산
2. schedule이 미래면 sleep, 이미 지났으면 behind schedule로 기록
3. `ingress_events.jsonl`에 emitted event record append
4. `replay.sqlite`에 input/event progress 기록
5. `model.stream.extract_online` 호출 및 user state/embedding snapshot index DB 기록
6. `online_embeddings.npz` row 수 확인
7. `model.stream.interest_assign` 호출 및 assignment/interest state/refit open DB 기록
8. 새로 append된 `refit_requests.jsonl` request를 읽음
9. request user별로 `model.stream.cluster_refit` 호출 및 refit lifecycle DB 갱신
10. `--recommend`가 있으면 event user에 대해 `model.stream.recommend_online` 호출 및 recommendation metadata DB 기록
11. `replay_events.jsonl`에 `stage=trace_event` progress/lag record append
12. 전체 완료 시 `replay_summary.json` 저장

주요 replay artifact:

- `replay_input_events.jsonl`
- `ingress_events.jsonl`
- `replay_events.jsonl`
- `replay.sqlite`
- `replay_summary.json`
- `online_embeddings.npz`
- `online_embedding_events.jsonl`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`
- `refit_events.jsonl`
- `stream_recommendations.jsonl`

`replay_summary.json`에는 `speed`, `traceSpanSec`, `scheduledSpanSec`, `targetEventsPerSec`, `throughputEventsPerSec`, `behindScheduleEvents`, `mean/max *LagSec`가 기록된다. `totals.activeEmbeddingRows`는 event별 active snapshot row 수를 합산한 값이며 최종 active embedding row 수가 아니다.

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
  -> summary.paths.replayDb
  -> outputs/stream/replay_demo/replay.sqlite
  -> summary.paths.*
  -> dashboard/cluster_dashboard.py Replay monitor
```

Dashboard는 replay/stream state를 만들거나 수정하지 않는다. Replay monitor는 `replay_summary.json`의 `paths` 값을 stable entrypoint로 삼고, `paths.replayDb`가 있으면 SQLite runtime store를 우선 사용한다. DB가 없으면 기존 JSONL artifact를 fallback으로 읽는다.

## 6. 현재 문제 포인트

### P0. Batch recommendation 계약 불일치

`model.batch.recommend`는 interest vector NPZ를 기대하지만, 현재 batch cluster의 interest vector 정본은 SQLite state store다. Batch 추천을 다시 실험하려면 이 bridge를 먼저 정리해야 한다.

수정 후보:

- `model.batch.recommend --state-db outputs/batch/state.sqlite` 지원
- 또는 `model.batch.export_interest_vectors` 같은 변환 entrypoint 추가

### Resolved. Refit request lifecycle이 append-only log에 의존

1차 SQLite runtime store 도입 후 trace replay 기준 refit lifecycle 정본은 `replay.sqlite`의 `refit_requests`와 `refit_attempts`다. `interest_assign`은 request를 `open`으로 만들고, `cluster_refit`은 `running -> closed/skipped/failed`로 갱신한다. 기존 `refit_requests.jsonl`과 `refit_events.jsonl`은 fallback/debug log로 남는다.

잔여 후보:

- standalone JSONL-only 운용을 계속 지원할지 결정
- skipped 이후 재시도 정책을 `retryAfterRawEventCount` 같은 명시 필드로 분리
- 오래된 open request를 supersede하는 정책을 더 큰 replay에서 검증

### P1. Refit skipped 상태 처리

SQLite lifecycle 기준으로 `skipped`는 terminal 상태로 기록된다. 현재 3-event smoke에서는 active row 부족으로 `skipped=1`이 기록되고 request는 더 이상 open으로 남지 않는다. 다만 skipped를 영구 종료로 볼지, 조건부 retry 대상으로 볼지는 아직 정책화가 덜 되어 있다.

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
- recommendation record에 `eventId`, `replayOrder`, 또는 `processedAt` replay context를 추가
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
- 로컬 `experiments/model/<run_id>/notes.md`에 artifact freshness를 기록하고, GitHub에 남길 결론은 `docs/`에 요약
- dashboard 기본 path가 demo artifact인지 production-scale artifact인지 명확히 표시

## 7. 바로 이어질 작업 제안

1. Representative contention replay를 여러 profile로 실행한다. 예: high `--speed`, refit on/off, `--recommend`, single-user 집중 replay.
2. `runtime_report` 결과를 기준으로 병목을 분류한다. stage latency, event lag, refit terminal status, repeated embedding signal을 우선 본다.
3. Streaming embedding snapshot/delta 의미를 분리한다. 최소한 replay summary와 assignment log에서 snapshot으로 인한 재처리 record를 구분한다.
4. Batch recommendation bridge를 정리한다. 현재 batch cluster 결과로 추천까지 이어지는 길이 끊겨 있으므로, `model.batch.recommend`가 JSON interest state를 읽게 하는 것이 가장 직접적이다.
5. 이후 실제 추천 품질 평가를 붙인다. Batch/stream 추천 둘 다 `seen` 제외, top-K, score normalization 정책을 같은 기준으로 비교해야 한다.
