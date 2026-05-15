# Streaming E2E Pipeline Handoff

이 문서는 현재 end-to-end streaming replay 파이프라인을 다른 작업자가 바로 실행하고, 단계별로 어떤 데이터가 넘어가는지 확인하기 위한 핸드오프 문서다.

프로젝트 운영 규칙은 `PROJECT_GUIDE.md`가 정본이고, replay/dashboard 파일 계약은 `docs/streaming-replay-dashboard-contract.md`가 정본이다. 이 문서는 실행과 협업을 위한 빠른 설명이다.

## Current Status

2026-05-15 기준으로 replay 공식 경로는 N배속 trace-clock runner + SQLite runtime store다. `--speed N`은 trace timestamp를 wall-clock으로 압축하며, event별 `scheduledAt`, `emittedAt`, lag, throughput을 같은 replay scope에 기록한다. Runtime state, payload, metadata, stage attempt, refit lifecycle은 `outputs/stream/replay_demo/replay.sqlite`에 기록하고, JSONL/JSON/NPZ artifact는 fallback/debug와 대형 vector 파일 정본으로 유지한다.

검증 command:

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

추천까지 포함한 실행 예시:

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
  --recommend \
  --recommend-top-k 20 \
  --run-id trace_replay_with_recommend
```

검증 결과:

| metric | value |
|---|---:|
| status | completed |
| input events | 5 |
| processed events | 5 |
| unique users | 1 |
| speed | 100 |
| trace span | 32 sec |
| scheduled span | 0.32 sec |
| assignment records | 10 |
| refit requests opened | 1 |
| refit requests closed | 0 (`--skip-refit`) |
| refit requests skipped | 0 |
| target EPS | 15.625 |
| actual EPS | about 0.158 |
| behind schedule events | 4 |
| elapsed | about 31.74 sec |

위 smoke는 trace schedule/lag 기록 확인을 위해 `--skip-refit`을 사용한다. refit까지 닫는 검증은 `--skip-refit`을 제거하고 `--cluster-backend auto` 또는 `cpu`를 지정한다.

## Temporal 2022 Runbook

Temporal streaming E2E는 아래 cutoff를 하나의 계약으로 사용한다.

```text
T = 2022-01-01T00:00:00Z
pre-T:  ratedAt < T
post-T: ratedAt >= T
```

신규 temporal run의 모델 관련 산출물은 `outputs/pre/temporal_2022/` 아래에 모으고, replay runtime 산출물은 `outputs/post/temporal_2022/`에 격리한다. 기존 `outputs/sasrec_cl.pt`, `outputs/item2idx.json`, `outputs/canonical_embeddings.npz` 같은 루트 경로는 default/legacy 호환 경로로 유지한다.

Pre-T 모델과 state 생성:

```bash
.venv/bin/python -m model.batch.train \
  --run-id temporal_2022 \
  --max-rated-at-exclusive 2022-01-01T00:00:00Z \
  --output-dir outputs/pre/temporal_2022

.venv/bin/python -m model.batch.extract_canonical \
  --run-id temporal_2022 \
  --max-rated-at-exclusive 2022-01-01T00:00:00Z \
  --checkpoint outputs/pre/temporal_2022/sasrec_cl.pt \
  --item2idx outputs/pre/temporal_2022/item2idx.json \
  --output outputs/pre/temporal_2022/canonical_embeddings.npz

.venv/bin/python -m model.batch.cluster \
  --run-id temporal_2022 \
  --embeddings outputs/pre/temporal_2022/canonical_embeddings.npz \
  --output outputs/pre/temporal_2022/user_interests.npz \
  --interest-state-dir outputs/pre/temporal_2022/interest_states

.venv/bin/python -m model.stream.seed_pre_t_state \
  --run-id temporal_2022 \
  --max-rated-at-exclusive 2022-01-01T00:00:00Z \
  --item2idx outputs/pre/temporal_2022/item2idx.json \
  --state-dir outputs/pre/temporal_2022/user_states \
  --interest-state-dir outputs/pre/temporal_2022/interest_states \
  --summary outputs/pre/temporal_2022/pre_summary.json
```

Post-T seeded replay:

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --run-id temporal_2022_replay \
  --output-root outputs/post/temporal_2022 \
  --reset-output \
  --generate-events \
  --start-rated-at 2022-01-01T00:00:00Z \
  --speed 100 \
  --checkpoint outputs/pre/temporal_2022/sasrec_cl.pt \
  --item2idx outputs/pre/temporal_2022/item2idx.json \
  --seed-user-state-dir outputs/pre/temporal_2022/user_states \
  --seed-interest-state-dir outputs/pre/temporal_2022/interest_states \
  --cluster-backend auto \
  --recommend
```

`seed_pre_t_state`는 `user_states/{user_id}.json`을 T 직전 상태로 만들고, `interest_states/{user_id}.json`이 이미 있으면 pre-T active `rawEventId`를 `processedRawEventIds`에 표시한다. 이렇게 해야 post-T 첫 이벤트 처리 때 pre-T active snapshot row가 새 이벤트처럼 assignment/refit 대상으로 보이지 않는다.

## Minimum Runbook

필수 입력:

- `data/ratings_drop_processed.jsonl`
- `data/movies_processed_drop.csv`
- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `.venv/`

replay binary가 없거나 오래되었으면 먼저 빌드한다.

```bash
make -C replay
```

작은 smoke는 위 Current Status command를 그대로 실행한다. 모든 trace replay 산출물은 아래 경로로 격리된다.

```text
outputs/stream/replay_demo/
```

기본 stream 산출물인 `outputs/stream/online_embeddings.npz`, `outputs/stream/user_states/`, `outputs/stream/interest_states/`를 덮어쓰지 않는다.

Replay가 끝난 뒤 runtime/control-plane 상태는 SQLite report로 바로 요약할 수 있다.

```bash
.venv/bin/python -m model.stream.runtime_report \
  --db outputs/stream/replay_demo/replay.sqlite \
  --top-events 10
```

이 report는 dominant stage latency, event lag, refit lifecycle, already-processed/repeated embedding signal, user state progress를 `replay.sqlite`만 보고 출력한다.

## Mental Model

```text
ratings_drop_processed.jsonl
  -> replay/bin/rating_replay
  -> replay_input_events.jsonl
  -> replay_pipeline --speed N trace clock
     -> replay.sqlite
     -> ingress_events.jsonl
     -> extract_online
        -> user_states/{user_id}.json
        -> online_embeddings.npz
        -> online_embedding_events.jsonl
     -> interest_assign
        -> interest_states/{user_id}.json
        -> interest_assignments.jsonl
        -> refit_requests.jsonl
     -> cluster_refit
        -> updated interest_states/{user_id}.json
        -> refit_events.jsonl
     -> recommend_online (when --recommend)
        -> stream_recommendations.jsonl
  -> replay_events.jsonl
  -> replay_summary.json
```

`replay_pipeline`은 새 모델링 로직을 직접 구현하지 않는다. replay input의 `ratedAtTs`를 기준으로 event별 scheduled wall-clock time을 계산하고, 기존 stream CLI를 event 단위로 호출하는 trace replay runner다. 각 stage CLI에는 `--runtime-db`와 `--event-id`를 넘겨 같은 SQLite store에 runtime/state payload를 dual-write한다.

## Stage 0. Replay Input Generation

Producer:

- `replay/bin/rating_replay`
- 또는 `python3 -m model.stream.replay_pipeline --generate-events`

Input:

- `data/ratings_drop_processed.jsonl`

Output:

- `outputs/stream/replay_demo/replay_input_events.jsonl`

한 줄은 replay할 rating event 하나다.

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

정렬 기준은 `ratedAtTs`, `userId`, `movieId`, `eventId`다. `eventId`는 replay input 파일 안에서 globally unique이고, `replayOrder`는 정렬 후 0-based 순서다.

## Stage 1. Trace-Clock Replay

Producer:

- `model/stream/replay_pipeline.py`

Input:

- `replay_input_events.jsonl`

Emit log:

- `outputs/stream/replay_demo/ingress_events.jsonl`

각 input event는 아래 schedule 기준으로 emitted 된다.

```text
scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / speed
```

`ingress_events.jsonl`은 emitted event와 schedule metric을 담는다.

```json
{
  "version": "stream_ingress_event.v1",
  "runId": "trace_replay_smoke",
  "eventId": 0,
  "replayOrder": 0,
  "userId": 28,
  "movieId": 296,
  "rating": 4.0,
  "ratedAt": "2001-01-01T00:00:00Z",
  "ratedAtTs": 978307200.0,
  "speed": 100.0,
  "scheduledAt": "2026-05-14T12:00:00.000000+09:00",
  "emittedAt": "2026-05-14T12:00:00.000271+09:00",
  "injectorLagSec": 0.000271,
  "behindSchedule": false
}
```

각 event마다 아래 순서가 실행된다.

```text
extract_online -> interest_assign -> cluster_refit
```

`--skip-refit`을 주면 `cluster_refit` 호출만 건너뛸 수 있다. `--recommend`를 주면 event 처리 이후 `recommend_online`이 추가로 실행된다.

## Stage 2. Online Extract

Consumer/producer:

- `model.stream.extract_online`

Input:

- single event JSON
- `data/movies_processed_drop.csv`
- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- 기존 `user_states/{user_id}.json`이 있으면 이어서 로드

Output:

- `user_states/{user_id}.json`
- `online_embeddings.npz`
- `online_embedding_events.jsonl`

동작:

1. 새 rating event를 평점과 무관하게 raw user state에 저장한다.
2. user의 전체 raw history를 시간순으로 다시 정렬한다.
3. 현재까지 관측된 rating 분포로 positive projection을 재계산한다.
4. active positive event만 SASRec canonical window로 embedding한다.
5. user state와 active embedding 전체 snapshot을 저장한다.

중요한 점:

- `rawEvents`는 들어온 rating event 전체다.
- `positiveEvents`는 현재 policy 기준 positive로 판정된 event다.
- `rawEventId`는 user별 stable id다.
- `eventIdx`는 positive projection 기준 derived id라 raw history가 늘면 재계산될 수 있다.
- `online_embeddings.npz`는 "이번 event delta"가 아니라 현재 user state의 active positive embedding 전체 snapshot이다.

`online_embeddings.npz` 주요 배열:

| key | meaning |
|---|---|
| `embeddings` | active positive embedding matrix, shape `(N, 128)` |
| `user_ids` | row별 user id |
| `raw_event_ids` | row별 raw event id |
| `event_idx` | positive sequence 기준 event index |
| `movie_ids` | row별 movie id |
| `rated_at_ts` | event timestamp seconds |
| `rated_at_iso` | event timestamp ISO string |
| `history_len` | embedding context length |
| `context_start_idx` | canonical window 시작 event index |
| `status` | 현재는 active row만 저장 |

## Stage 3. Interest Assign

Consumer/producer:

- `model.stream.interest_assign`

Input:

- `online_embeddings.npz`
- 기존 `interest_states/{user_id}.json`이 있으면 이어서 로드

Output:

- `interest_states/{user_id}.json`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`
- `replay.sqlite`의 `interest_states`, `interest_vectors`, `assignments`, `refit_requests`

동작:

1. `online_embeddings.npz`의 active row를 user별로 읽는다.
2. 기존 interest vector가 없으면 row를 assign하지 않고 pending으로 쌓는다.
3. pending event 수가 `--refit-min-events` 이상이면 open refit request를 남긴다.
4. interest vector가 있으면 cosine similarity로 가장 가까운 interest에 assign한다.
5. similarity가 낮거나 invalid하면 outlier/pending으로 쌓고 trigger 기준을 확인한다.

assignment status:

| status | meaning |
|---|---|
| `pending_no_interest` | interest state가 없어 refit 전까지 pending |
| `pending_refit_required` | pending 누적으로 refit 필요 |
| `assigned` | 기존 interest에 정상 assign |
| `already_processed` | 이전 refit/assign에서 이미 처리한 raw event |
| `outlier` | similarity threshold 미만 또는 invalid similarity |

`refit_requests.jsonl`은 fallback/debug용 append-only 후보 log다. Trace replay에서 현재 open/running/closed/skipped/failed request의 정본은 SQLite `refit_requests` table이다. 현재 구현은 user별 `refit_request_open` flag와 DB lifecycle로 중복 open request를 막는다.

## Stage 4. Cluster Refit

Consumer/producer:

- `model.stream.cluster_refit`

Input:

- `refit_requests.jsonl`
- `online_embeddings.npz`
- 기존 `interest_states/{user_id}.json`

Output:

- updated `interest_states/{user_id}.json`
- `refit_events.jsonl`
- `replay.sqlite`의 `refit_requests`, `refit_attempts`, `interest_states`, `interest_vectors`

동작:

1. open refit request를 읽는다.
2. request user의 active embedding 전체를 모은다.
3. `--cluster-backend auto|gpu|cpu`에 따라 backend를 고른다.
4. UMAP + HDBSCAN으로 active embeddings를 clustering한다.
5. label `-1` noise는 interest vector에서 제외한다.
6. 전부 noise거나 sample 부족이면 전체 mean fallback interest 1개를 만든다.
7. 기존 interest vector를 replace하고 pending/refit flags를 clear한다.
8. request 처리 결과를 DB lifecycle에 `closed`, `skipped`, `failed`로 반영하고 `refit_events.jsonl`에도 fallback/debug log를 남긴다.

backend behavior:

- `cpu`: 항상 CPU `umap-learn + hdbscan`
- `gpu`: RAPIDS/cuML + CUDA runtime이 안 되면 실패
- `auto`: cuML import와 CUDA runtime probe가 통과하면 GPU, 아니면 CPU fallback. auto GPU refit 실행 중 실패해도 CPU로 한 번 fallback

## Stage 4-1. Online Recommendation

Consumer/producer:

- `model.stream.recommend_online`

Input:

- `interest_states/{user_id}.json`
- `user_states/{user_id}.json`
- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `data/movies_processed_drop.csv`

Output:

- `stream_recommendations.jsonl`
- `replay.sqlite`의 `recommendation_runs`, `recommendation_rows`

동작:

1. refit/assign 결과로 만들어진 interest vector를 읽는다.
2. SASRec item embedding과 item vocabulary를 로드한다.
3. user state의 positive movie를 seen set으로 보고 기본적으로 추천 후보에서 제외한다.
4. `score(u, i) = max_k(u_k^T v_i)`로 item을 scoring한다.
5. `--recommend-top-k` 개수만큼 JSONL에 append한다.

이 단계는 `replay_pipeline --recommend`를 사용할 때만 실행된다. 추천 결과 평가(Recall@K/NDCG@K)는 아직 별도 파이프라인으로 구현되지 않았다.

## Stage 5. Replay Summary And Dashboard Entry

Consumer/producer:

- `model.stream.replay_pipeline`
- dashboard는 read-only consumer

Output:

- `replay.sqlite`
- `ingress_events.jsonl`
- `replay_events.jsonl`
- `replay_summary.json`

`replay.sqlite`는 replay runtime/state/control-plane store다. `runs`, `input_events`, `event_progress`, `stage_attempts`, `runtime_metrics`, `user_states`, `interest_states`, `assignments`, `refit_requests`, `refit_attempts`, `embedding_snapshots`, `embedding_rows`, `recommendation_runs`, `recommendation_rows`를 기록한다. 대형 vector/checkpoint/NPZ artifact는 파일 정본으로 유지하고, SQLite에는 path, dtype, shape, row index 같은 metadata를 둔다.

`ingress_events.jsonl`은 event 주입 시각과 trace clock 기준 schedule/lag를 append한다.

`replay_events.jsonl`은 progress log다. event별 processed count, active row count, assignment status count, refit close/skip count, injector/processing/end-to-end latency를 append한다.

`replay_summary.json`은 dashboard가 읽는 stable entrypoint다.

```json
{
  "version": "stream_trace_replay_summary.v1",
  "runId": "trace_replay_smoke",
  "status": "completed",
  "inputEvents": 5,
  "processedEvents": 5,
  "uniqueUsers": 1,
  "speed": 100.0,
  "traceSpanSec": 32.0,
  "scheduledSpanSec": 0.32,
  "targetEventsPerSec": 15.625,
  "throughputEventsPerSec": 0.158,
  "refitBackend": "auto",
  "paths": {
    "replayDb": "outputs/stream/replay_demo/replay.sqlite"
  },
  "totals": {
    "activeEmbeddingRows": 10,
    "assignmentRecords": 10,
    "refitRequestsOpened": 1,
    "refitClosed": 0,
    "refitSkipped": 0,
    "recommendationRows": 0,
    "behindScheduleEvents": 4,
    "maxInjectorLagSec": 25.26,
    "maxProcessingLagSec": 6.94,
    "maxEndToEndLagSec": 31.42
  }
}
```

주의: `totals.activeEmbeddingRows`는 event 처리 후 관측한 active snapshot row 수의 누적 합이다. 최종 active row 수를 보려면 `online_embeddings.npz`의 `embeddings.shape[0]` 또는 마지막 `online_embedding_events.jsonl` record를 확인한다.

## Artifact Map

| artifact | written by | consumed by | role |
|---|---|---|---|
| `replay_input_events.jsonl` | replay generator | `replay_pipeline` | timestamp-sorted replay source |
| `replay.sqlite` | `replay_pipeline`, stream stages | dashboard/humans | runtime state, payload, metadata, lifecycle, metric store |
| `runtime_report` output | `model.stream.runtime_report` | humans/notes | optional markdown/json bottleneck report from `replay.sqlite` |
| `ingress_events.jsonl` | `replay_pipeline` | dashboard/humans | trace-clock event emit log |
| `user_states/{user_id}.json` | `extract_online` | `extract_online` | raw events + positive projection state |
| `online_embeddings.npz` | `extract_online` | `interest_assign`, `cluster_refit` | current active positive embedding snapshot |
| `online_embedding_events.jsonl` | `extract_online` | humans/debugging | online extract run summary log |
| `interest_states/{user_id}.json` | `interest_assign`, `cluster_refit` | `interest_assign`, `cluster_refit`, dashboard | interest vectors and trigger state |
| `interest_assignments.jsonl` | `interest_assign` | `replay_pipeline`, dashboard | assignment/pending/outlier log |
| `refit_requests.jsonl` | `interest_assign` | `cluster_refit`, dashboard fallback | fallback/debug refit request log |
| `refit_events.jsonl` | `cluster_refit` | `replay_pipeline`, dashboard fallback | fallback/debug refit close/skip result log |
| `stream_recommendations.jsonl` | `recommend_online` | dashboard | replay-scoped top-K recommendation log |
| `replay_events.jsonl` | `replay_pipeline` | dashboard | replay progress log |
| `replay_summary.json` | `replay_pipeline` | dashboard | stable replay entrypoint |

## Extension Points

새 event source를 붙일 때:

- `replay_input_events.jsonl`과 같은 필드를 만들거나, `extract_online --event-json`이 읽는 event shape인 `userId`, `movieId`, `rating`, `ratedAt`을 제공한다.
- event ordering은 timestamp 기준으로 안정적이어야 한다.

positive policy를 바꿀 때:

- `model/stream/state.py`의 positive projection 정책을 수정한다.
- raw event 보존 계약은 유지한다.
- `eventIdx`가 derived id라는 점을 downstream에 계속 명시한다.

embedding model을 바꿀 때:

- `extract_online`의 output NPZ key와 shape 계약을 유지하거나 변경 전에 새 계약 문서를 만든다.
- `interest_assign`과 `cluster_refit`은 `embeddings` row와 metadata arrays 길이가 같다고 가정한다.

assignment trigger를 바꿀 때:

- `interest_assign.py`에서 status/reason/refit request 필드를 유지하는 방향으로 확장한다.
- 새 status를 추가하면 dashboard와 이 문서를 함께 갱신한다.

refit algorithm을 바꿀 때:

- `cluster_refit.py`의 input/output 계약을 유지한다.
- `interest_states/{user_id}.json`의 `interests[].vector` dimension은 online embedding dimension과 같아야 한다.
- `refit_events.jsonl`에는 backend, status, request, activeEmbeddingRows, interestCount를 남긴다.

recommendation logic을 바꿀 때:

- `stream_recommendations.jsonl`의 user/movie/rank/score 필드를 유지하거나 계약을 먼저 갱신한다.
- seen item 제외 정책을 바꾸면 `stream/recommend_online.py`, `docs/streaming-replay-dashboard-contract.md`, dashboard README를 함께 갱신한다.

dashboard를 바꿀 때:

- `replay_summary.json`의 `paths`를 우선 사용한다.
- `paths.replayDb`가 있으면 SQLite를 우선 읽고, 기존 JSONL/JSON은 fallback으로 사용한다.
- replay/stream 내부 Python 함수에 직접 의존하지 않는다.
- dashboard는 replay artifact를 수정하지 않는다.

## Known Gaps

- 현재 smoke는 user 28, 5 events 기준의 작은 trace-clock 검증이다.
- `u_k` 기반 추천 scoring은 `stream/recommend_online.py`와 `replay_pipeline --recommend`로 가능하다. 다만 Recall@K/NDCG@K 같은 offline evaluation은 아직 없다.
- replay는 event마다 full active snapshot을 다시 assign/refit 후보로 읽으므로 `already_processed` record가 정상적으로 생긴다.
- `outputs/stream/replay_demo/`는 demo root 하나를 재사용한다. 여러 사람이 동시에 다른 실험을 돌릴 때는 `--output-root outputs/stream/replay_demo_<name>`처럼 별도 root를 쓰는 것이 안전하다.
- GPU backend는 환경 의존적이다. `auto`를 쓰면 가능한 경우 GPU를 쓰고, 현재 로컬처럼 CUDA runtime이 맞지 않으면 CPU fallback으로 진행한다.
