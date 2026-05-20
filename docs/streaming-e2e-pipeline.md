# Streaming E2E Pipeline Handoff

이 문서는 현재 end-to-end streaming replay 파이프라인을 다른 작업자가 바로 실행하고, 단계별로 어떤 데이터가 넘어가는지 확인하기 위한 핸드오프 문서다.

프로젝트 운영 규칙은 `PROJECT_GUIDE.md`가 정본이고, replay/dashboard 파일 계약은 `docs/streaming-replay-dashboard-contract.md`가 정본이다. 이 문서는 실행과 협업을 위한 빠른 설명이다.

## Current Status

2026-05-19 기준으로 replay 공식 경로는 N배속 trace-clock runner + mode별 SQLite output root다. `--speed N`은 trace timestamp를 wall-clock으로 압축하며, event별 `scheduledAt`, `emittedAt`, lag, throughput을 같은 replay scope에 기록한다. `--history-mode off` 기본 실행은 `outputs/post/<run_id>_production/production/production.sqlite`에 runtime state, payload, metadata, stage attempt, refit lifecycle, post replay active online embedding cache를 기록한다. `--history-mode history` 실행은 별도 `outputs/post/<run_id>_history/` root에 production current state, append-only history DB, dashboard compact DB를 만든다. JSONL/JSON artifact는 debug로 유지하고, post replay의 `online_embeddings.npz`는 `--export-online-embeddings-npz`를 줄 때만 생성하는 debug/export 산출물이다.

검증 command:

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --reset-output \
  --generate-events \
  --replay-user-id 28 \
  --limit-events 5 \
  --speed 100 \
  --history-mode off \
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
  --history-mode history \
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

신규 temporal run의 모델 관련 산출물은 `outputs/pre/temporal_2022/` 아래에 모은다. Replay runtime 산출물은 mode가 드러나도록 `outputs/post/<run_id>_production/` 또는 `outputs/post/<run_id>_history/` root에 격리한다. 기존 `outputs/post/temporal_2022_events_<N>/`, `outputs/post/temporal_2022_full/`, `outputs/sasrec_cl.pt`, `outputs/item2idx.json`, `outputs/canonical_embeddings.npz` 같은 경로는 default/legacy 호환 경로로 유지한다.

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
  --state-db outputs/pre/temporal_2022/state.sqlite \
  --reset-state-db

.venv/bin/python -m model.stream.seed_pre_t_state \
  --run-id temporal_2022 \
  --max-rated-at-exclusive 2022-01-01T00:00:00Z \
  --item2idx outputs/pre/temporal_2022/item2idx.json \
  --state-db outputs/pre/temporal_2022/state.sqlite \
  --summary outputs/pre/temporal_2022/pre_summary.json
```

Post-T seeded replay:

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --run-id temporal_2022_events_1000 \
  --history-mode history \
  --reset-output \
  --generate-events \
  --start-rated-at 2022-01-01T00:00:00Z \
  --limit-events 1000 \
  --speed 100 \
  --checkpoint outputs/pre/temporal_2022/sasrec_cl.pt \
  --item2idx outputs/pre/temporal_2022/item2idx.json \
  --seed-state-db outputs/pre/temporal_2022/state.sqlite \
  --seed-run-id temporal_2022 \
  --cluster-backend auto \
  --recommend
```

`--limit-events`를 쓰는 replay는 run id에 `events_<N>`을 넣는다. `--history-mode history`를 쓰면 기본 output root는 `outputs/post/<run_id>_history/`가 되고, `--history-mode off`를 쓰면 `outputs/post/<run_id>_production/`이 된다. 전체 post-T replay는 run id에 `full`을 넣어 smoke/partial run과 분리한다. `--reset-output`은 지정한 output root를 지우고 다시 만들기 때문에, 보존할 결과는 새 root 이름으로 실행한다.

`seed_pre_t_state`는 `state.sqlite`의 compressed `user_states` payload를 T 직전 상태로 만들고, 같은 DB에 batch cluster interest state가 있으면 pre-T active `rawEventId`를 `processedRawEventIds`에 표시한다. pre seed DB는 replay 시작점 복원용이므로 `user_raw_events`, `user_positive_events` row를 펼쳐 저장하지 않는다. post-T replay는 이 DB를 복사하지 않는다. 각 stream stage가 post production DB에서 state를 먼저 찾고, 없으면 pre `state.sqlite`에서 lazy-load한 뒤 touched user만 post DB에 기록한다.

### Temporal Verification Boundary

이 runbook은 temporal cutoff와 artifact 경로 계약의 정본이다. 현재 문서화된 구현 smoke는 cutoff helper, CLI option, SQLite seed fallback, interest processed marker, replay/runtime store 경로를 확인한 상태다. 다만 full temporal chain인 pre-T train -> canonical extract -> cluster -> seed state -> post-T seeded replay는 실행 환경과 시간이 필요하므로 별도 run으로 검증해야 한다.

`outputs/pre/temporal_2022/`나 `outputs/post/temporal_2022_*` 아래 기존 로컬 artifact는 freshness가 섞여 있을 수 있다. 새 결과를 인용하거나 공유할 때는 새 `--run-id`와 새 output root를 쓰고, 대표 결론만 `docs/`에 요약한다.

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
outputs/post/trace_replay_smoke_production/
```

기본 stream 산출물인 `outputs/stream/online_embeddings.npz`와 state DB/legacy state directory를 덮어쓰지 않는다.

Replay가 끝난 뒤 runtime/control-plane 상태는 SQLite report로 바로 요약할 수 있다.

```bash
.venv/bin/python -m model.stream.runtime_report \
  --db outputs/post/trace_replay_smoke_production/production/production.sqlite \
  --top-events 10
```

이 report는 dominant stage latency, event lag, refit lifecycle, already-processed/repeated embedding signal, user state progress를 production DB만 보고 출력한다.

## Mental Model

```text
ratings_drop_processed.jsonl
  -> replay/bin/rating_replay
  -> production/replay_input_events.jsonl
  -> replay_pipeline --speed N trace clock
     -> production/production.sqlite
     -> production/ingress_events.jsonl
     -> extract_online
        -> production DB user state + active_embedding_cache
        -> production/online_embedding_events.jsonl
     -> interest_assign
        -> production DB interest state (changed cache rows)
        -> production/interest_assignments.jsonl
        -> production/refit_requests.jsonl
     -> cluster_refit
        -> updated production DB interest state (full active cache rows)
        -> production/refit_events.jsonl
     -> recommend_online (when --recommend)
        -> production/stream_recommendations.jsonl
     -> history/history.sqlite (when --history-mode history)
  -> production/replay_events.jsonl
  -> replay_summary.json
  -> dashboard_compact/dashboard_compact.sqlite (history mode only)
```

`replay_pipeline`은 새 모델링 로직을 직접 구현하지 않는다. replay input의 `ratedAtTs`를 기준으로 event별 scheduled wall-clock time을 계산하고, `ReplayInProcessWorker`로 stream stage helper를 같은 Python process 안에서 호출하는 trace replay runner다. Stage별 Python subprocess는 띄우지 않는다. Standalone `extract_online`, `interest_assign`, `cluster_refit`, `recommend_online` CLI는 수동 실행/디버그 경로로 유지한다.

실행 중 콘솔 로그에는 `Replay progress i/N` 형태로 현재 처리 중인 event와 완료된 event 요약을 출력한다. 1000개 이상 replay를 돌릴 때 현재 몇 번째 event에서 시간을 쓰는지 확인하는 용도다.

## Stage 0. Replay Input Generation

Producer:

- `replay/bin/rating_replay`
- 또는 `python3 -m model.stream.replay_pipeline --generate-events`

Input:

- `data/ratings_drop_processed.jsonl`

Output:

- `outputs/post/<run_id>_production/production/replay_input_events.jsonl`

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

- `outputs/post/<run_id>_production/production/ingress_events.jsonl`

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
- 기존 SQLite user state가 있으면 이어서 로드하고, 없으면 `--seed-state-db`에서 lazy-load

Output:

- SQLite user state in `--runtime-db`/`--state-db`
- `production/production.sqlite`의 `active_embedding_cache` and `embedding_cache_changes` in replay runtime mode
- `online_embeddings.npz` only when explicitly exporting/debugging
- `online_embedding_events.jsonl`

동작:

1. 새 rating event를 평점과 무관하게 raw user state에 저장한다.
2. user의 전체 raw history를 시간순으로 다시 정렬한다.
3. 현재까지 관측된 rating 분포로 positive projection을 재계산한다.
4. active positive event의 signature를 계산한다.
5. replay runtime cache에서 signature가 없거나 바뀐 row만 SASRec canonical window로 embedding한다.
6. user state와 active embedding cache를 저장하고, 더 이상 active가 아닌 cached row는 inactive 처리한다.

중요한 점:

- `rawEvents`는 들어온 rating event 전체다.
- `positiveEvents`는 현재 policy 기준 positive로 판정된 event다.
- `rawEventId`는 user별 stable id다.
- `eventIdx`는 positive projection 기준 derived id라 raw history가 늘면 재계산될 수 있다.
- post replay 기본 경로에서 `online_embeddings.npz`는 쓰지 않는다.
- SQLite `active_embedding_cache`는 최신 active row의 정본이고, `embedding_cache_changes`는 해당 event에서 새로 insert/update된 row 목록이다.
- `--export-online-embeddings-npz`를 주면 debug/export용 `online_embeddings.npz`도 함께 쓴다.

debug/export `online_embeddings.npz` 주요 배열:

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

- replay runtime 기본 경로: production DB `active_embedding_cache`의 해당 event changed active rows
- legacy/debug 경로: `online_embeddings.npz`
- 기존 SQLite interest state가 있으면 이어서 로드하고, 없으면 `--seed-state-db`에서 lazy-load

Output:

- SQLite interest state in `--runtime-db`/`--state-db`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`
- production DB의 `interest_states`, `interest_vectors`, `assignments`, `refit_requests`

동작:

1. replay runtime에서는 SQLite cache의 changed active row를, legacy/debug 경로에서는 `online_embeddings.npz`의 active row를 user별로 읽는다.
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
- replay runtime 기본 경로: production DB `active_embedding_cache`의 request user full active rows
- legacy/debug 경로: `online_embeddings.npz`
- 기존 SQLite interest state

Output:

- updated SQLite interest state
- `refit_events.jsonl`
- production DB의 `refit_requests`, `refit_attempts`, `interest_states`, `interest_vectors`

동작:

1. open refit request를 읽는다.
2. request user의 active embedding 전체를 SQLite cache 또는 legacy NPZ에서 모은다.
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

- SQLite interest state
- SQLite user state
- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `data/movies_processed_drop.csv`

Output:

- `stream_recommendations.jsonl`
- production DB의 `recommendation_runs`, `recommendation_rows`

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
- `model.stream.compact_dashboard` when `--history-mode history`
- dashboard/API/frontend are read-only consumers

Output:

- `outputs/post/<run_id>_production/replay_summary.json`
- `outputs/post/<run_id>_production/production/production.sqlite`
- `outputs/post/<run_id>_production/production/*.jsonl`
- `outputs/post/<run_id>_history/replay_summary.json`
- `outputs/post/<run_id>_history/production/production.sqlite`
- `outputs/post/<run_id>_history/history/history.sqlite`
- `outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite`

`production/production.sqlite`는 replay runtime/state/control-plane store다. `runs`, `input_events`, `event_progress`, `stage_attempts`, `runtime_metrics`, `user_states`, `interest_states`, `assignments`, `refit_requests`, `refit_attempts`, `active_embedding_cache`, `embedding_cache_changes`, `embedding_snapshots`, `embedding_rows`, `recommendation_runs`, `recommendation_rows`를 기록한다. Post replay active online embedding은 SQLite cache가 정본이다. 대형 batch vector/checkpoint artifact는 파일 정본으로 유지하고, SQLite에는 payload, lifecycle, metric, 작은 vector/cache BLOB, artifact metadata를 둔다.

`history/history.sqlite`는 history mode에서만 쓰는 append-only side log다. `dashboard_compact/dashboard_compact.sqlite`는 history DB를 입력으로 만든 dashboard 전용 projection이다.

`replay_summary.json`은 replay/report용 stable entrypoint이며, compact dashboard는 `paths.dashboardCompactDb`를 찾을 때만 선택적으로 사용한다. Dashboard 화면 자체는 compact DB의 네 table을 읽는다.

```json
{
  "version": "stream_trace_replay_summary.v1",
  "runId": "trace_replay_smoke",
  "status": "completed",
  "inputEvents": 5,
  "processedEvents": 5,
  "uniqueUsers": 1,
  "speed": 100.0,
  "historyMode": "history",
  "paths": {
    "productionDb": "outputs/post/trace_replay_smoke_history/production/production.sqlite",
    "historyDb": "outputs/post/trace_replay_smoke_history/history/history.sqlite",
    "dashboardCompactDb": "outputs/post/trace_replay_smoke_history/dashboard_compact/dashboard_compact.sqlite"
  }
}
```

주의: `totals.activeEmbeddingRows`는 event 처리 중 새로 insert/update된 active cache row 수의 누적 합이다. 최종 active row 수를 보려면 `runtime_report`의 `cacheActiveRows` 또는 production DB의 `active_embedding_cache`에서 `status='active'` row 수를 확인한다.

## Artifact Map

| artifact | written by | consumed by | role |
|---|---|---|---|
| `production/replay_input_events.jsonl` | replay generator | `replay_pipeline` | timestamp-sorted replay source |
| `production/production.sqlite` | `replay_pipeline`, stream stages | `runtime_report`, humans/debug | runtime state, payload, metadata, lifecycle, metric store |
| `pre/state.sqlite` | `model.batch.cluster`, `seed_pre_t_state` | `replay_pipeline` stream stages | pre-T user/interest seed state store |
| `runtime_report` output | `model.stream.runtime_report` | humans/notes | optional markdown/json bottleneck report from production DB |
| `production/ingress_events.jsonl` | `replay_pipeline` | humans/debug | trace-clock event emit log |
| `production/production.sqlite.user_states` | `extract_online` | `extract_online`, `recommend_online` | touched raw events + positive projection state |
| `production/production.sqlite.active_embedding_cache` | `extract_online` | `interest_assign`, `cluster_refit` | post replay latest active positive embedding cache |
| `production/production.sqlite.embedding_cache_changes` | `extract_online` | `interest_assign`, humans/debug | per-event changed cache rows for delta assignment |
| `production/online_embeddings.npz` | `extract_online` | legacy/debug consumers | optional debug/export active positive embedding snapshot |
| `production/interest_assignments.jsonl` | `interest_assign` | humans/debug | assignment/pending/outlier log |
| `production/refit_requests.jsonl` | `interest_assign` | humans/debug | fallback/debug refit request log |
| `production/refit_events.jsonl` | `cluster_refit` | humans/debug | fallback/debug refit close/skip result log |
| `production/stream_recommendations.jsonl` | `recommend_online` | humans/debug | replay-scoped top-K recommendation log |
| `production/replay_events.jsonl` | `replay_pipeline` | humans/debug | replay progress log |
| `replay_summary.json` | `replay_pipeline` | reports/dashboard discovery | stable replay summary and compact DB path discovery |
| `history/history.sqlite` | `history_store` via replay pipeline | `compact_dashboard`, humans/debug | append-only event/state-version history |
| `dashboard_compact/dashboard_compact.sqlite` | `compact_dashboard` | Streamlit dashboard, FastAPI API | official POST replay dashboard display input |

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
- SQLite interest state payload의 `interests[].vector` dimension은 online embedding dimension과 같아야 한다.
- `refit_events.jsonl`에는 backend, status, request, activeEmbeddingRows, interestCount를 남긴다.

recommendation logic을 바꿀 때:

- `stream_recommendations.jsonl`의 user/movie/rank/score 필드를 유지하거나 계약을 먼저 갱신한다.
- seen item 제외 정책을 바꾸면 `stream/recommend_online.py`, `docs/streaming-replay-dashboard-contract.md`, dashboard README를 함께 갱신한다.

dashboard/API/frontend를 바꿀 때:

- POST replay display input은 `dashboard_compact/dashboard_compact.sqlite`로 유지한다.
- `replay_summary.json`은 `paths.dashboardCompactDb` discovery에만 선택적으로 사용한다.
- `production/production.sqlite`, `history/history.sqlite`, legacy `replay.sqlite`, JSONL debug artifact를 display fallback으로 직접 읽지 않는다.
- replay/stream 내부 Python 함수에 직접 의존하지 않는다.
- dashboard/API/frontend는 replay artifact를 수정하지 않는다.

## Known Gaps

- 현재 smoke는 user 28, 5 events 기준의 작은 trace-clock 검증이다.
- `u_k` 기반 추천 scoring은 `stream/recommend_online.py`와 `replay_pipeline --recommend`로 가능하다. 다만 Recall@K/NDCG@K 같은 offline evaluation은 아직 없다.
- replay는 event마다 full active snapshot을 다시 assign/refit 후보로 읽으므로 `already_processed` record가 정상적으로 생긴다.
- 기본 output root는 `outputs/post/<run_id>_production/` 또는 `outputs/post/<run_id>_history/`다. 여러 사람이 동시에 다른 실험을 돌릴 때는 고유한 `--run-id`나 `--output-root`를 쓰는 것이 안전하다.
- GPU backend는 환경 의존적이다. `auto`를 쓰면 가능한 경우 GPU를 쓰고, 현재 로컬처럼 CUDA runtime이 맞지 않으면 CPU fallback으로 진행한다.
