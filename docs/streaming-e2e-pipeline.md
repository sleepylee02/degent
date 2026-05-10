# Streaming E2E Pipeline Handoff

이 문서는 현재 end-to-end streaming replay 파이프라인을 다른 작업자가 바로 실행하고, 단계별로 어떤 데이터가 넘어가는지 확인하기 위한 핸드오프 문서다.

프로젝트 운영 규칙은 `PROJECT_GUIDE.md`가 정본이고, replay/dashboard 파일 계약은 `docs/streaming-replay-dashboard-contract.md`가 정본이다. 이 문서는 실행과 협업을 위한 빠른 설명이다.

## Current Status

2026-05-10 기준으로 replay closed-loop smoke는 완료된다.

검증 command:

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --reset-output \
  --generate-events \
  --replay-user-id 28 \
  --limit-events 30 \
  --micro-batch-size 15 \
  --refit-min-events 3 \
  --assign-trigger-count 3 \
  --outlier-trigger-count 3 \
  --min-cluster-size 2 \
  --cluster-dim 3 \
  --cluster-backend auto \
  --run-id e2e_streaming_smoke_auto_fallback
```

검증 결과:

| metric | value |
|---|---:|
| status | completed |
| input events | 30 |
| processed events | 30 |
| unique users | 1 |
| micro-batches | 2 |
| assignment records | 19 |
| refit requests opened | 2 |
| refit requests closed | 2 |
| refit requests skipped | 0 |
| elapsed | about 32.40 sec |

현재 로컬 환경에서는 GPU runtime이 `cudaErrorInsufficientDriver`로 실패하므로 `--cluster-backend auto`가 CPU fallback을 사용했다. GPU가 정상인 환경에서는 같은 옵션으로 GPU refit을 시도한다.

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

작은 smoke는 위 Current Status command를 그대로 실행한다. 모든 demo 산출물은 아래 경로로 격리된다.

```text
outputs/stream/replay_demo/
```

기본 stream 산출물인 `outputs/stream/online_embeddings.npz`, `outputs/stream/user_states/`, `outputs/stream/interest_states/`를 덮어쓰지 않는다.

## Mental Model

```text
ratings_drop_processed.jsonl
  -> replay/bin/rating_replay
  -> replay_input_events.jsonl
  -> replay_pipeline micro-batches
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
  -> replay_events.jsonl
  -> replay_summary.json
```

`replay_pipeline`은 새 모델링 로직을 직접 구현하지 않는다. replay input을 micro-batch로 자른 뒤 기존 stream CLI를 순서대로 호출하는 orchestrator다.

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

## Stage 1. Micro-Batch Orchestration

Producer:

- `model/stream/replay_pipeline.py`

Input:

- `replay_input_events.jsonl`

Internal batch files:

- `outputs/stream/replay_demo/batches/batch_000000.jsonl`
- `outputs/stream/replay_demo/batches/batch_000001.jsonl`

batch 파일은 `extract_online`이 필요한 최소 event 필드만 담는다.

```json
{"userId": 28, "movieId": 296, "rating": 4.0, "ratedAt": "2001-01-01T00:00:00Z"}
```

각 micro-batch마다 아래 순서가 실행된다.

```text
extract_online -> interest_assign -> cluster_refit
```

`--skip-refit`을 주면 `cluster_refit` 호출만 건너뛸 수 있다.

## Stage 2. Online Extract

Consumer/producer:

- `model.stream.extract_online`

Input:

- batch event JSONL
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
- `online_embeddings.npz`는 "이번 batch delta"가 아니라 현재 user state의 active positive embedding 전체 snapshot이다.

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

`refit_requests.jsonl`은 append-only 후보 log다. 현재 구현은 user별 `refit_request_open` flag로 중복 open request를 막는다.

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

동작:

1. open refit request를 읽는다.
2. request user의 active embedding 전체를 모은다.
3. `--cluster-backend auto|gpu|cpu`에 따라 backend를 고른다.
4. UMAP + HDBSCAN으로 active embeddings를 clustering한다.
5. label `-1` noise는 interest vector에서 제외한다.
6. 전부 noise거나 sample 부족이면 전체 mean fallback interest 1개를 만든다.
7. 기존 interest vector를 replace하고 pending/refit flags를 clear한다.
8. request 처리 결과를 `refit_events.jsonl`에 `closed` 또는 `skipped`로 남긴다.

backend behavior:

- `cpu`: 항상 CPU `umap-learn + hdbscan`
- `gpu`: RAPIDS/cuML + CUDA runtime이 안 되면 실패
- `auto`: cuML import와 CUDA runtime probe가 통과하면 GPU, 아니면 CPU fallback. auto GPU refit 실행 중 실패해도 CPU로 한 번 fallback

## Stage 5. Replay Summary And Dashboard Entry

Consumer/producer:

- `model.stream.replay_pipeline`
- dashboard는 read-only consumer

Output:

- `replay_events.jsonl`
- `replay_summary.json`

`replay_events.jsonl`은 progress log다. micro-batch별 processed count, active row count, assignment status count, refit close/skip count, latency를 append한다.

`replay_summary.json`은 dashboard가 읽는 stable entrypoint다.

```json
{
  "version": "stream_replay_summary.v1",
  "runId": "e2e_streaming_smoke_auto_fallback",
  "status": "completed",
  "inputEvents": 30,
  "processedEvents": 30,
  "uniqueUsers": 1,
  "microBatchSize": 15,
  "refitBackend": "auto",
  "totals": {
    "activeEmbeddingRows": 19,
    "assignmentRecords": 19,
    "refitRequestsOpened": 2,
    "refitClosed": 2,
    "refitSkipped": 0
  }
}
```

주의: `totals.activeEmbeddingRows`는 micro-batch별 active snapshot row 수를 더한 값이다. 최종 active row 수를 보려면 `online_embeddings.npz`의 `embeddings.shape[0]` 또는 마지막 `online_embedding_events.jsonl` record를 확인한다. 2026-05-10 smoke에서는 batch별 active row가 7, 12라 summary total은 19이고 최종 active row는 12다.

## Artifact Map

| artifact | written by | consumed by | role |
|---|---|---|---|
| `replay_input_events.jsonl` | replay generator | `replay_pipeline` | timestamp-sorted replay source |
| `batches/batch_*.jsonl` | `replay_pipeline` | `extract_online` | micro-batch event input |
| `user_states/{user_id}.json` | `extract_online` | `extract_online` | raw events + positive projection state |
| `online_embeddings.npz` | `extract_online` | `interest_assign`, `cluster_refit` | current active positive embedding snapshot |
| `online_embedding_events.jsonl` | `extract_online` | humans/debugging | online extract run summary log |
| `interest_states/{user_id}.json` | `interest_assign`, `cluster_refit` | `interest_assign`, `cluster_refit`, dashboard | interest vectors and trigger state |
| `interest_assignments.jsonl` | `interest_assign` | `replay_pipeline`, dashboard | assignment/pending/outlier log |
| `refit_requests.jsonl` | `interest_assign` | `cluster_refit`, dashboard | open refit request log |
| `refit_events.jsonl` | `cluster_refit` | `replay_pipeline`, dashboard | refit close/skip result log |
| `replay_events.jsonl` | `replay_pipeline` | dashboard | replay progress log |
| `replay_summary.json` | `replay_pipeline` | dashboard | stable replay entrypoint |

## Extension Points

새 event source를 붙일 때:

- `replay_input_events.jsonl`과 같은 필드를 만들거나, `extract_online`이 읽는 batch event shape인 `userId`, `movieId`, `rating`, `ratedAt`을 제공한다.
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

dashboard를 바꿀 때:

- `replay_summary.json`의 `paths`를 우선 사용한다.
- replay/stream 내부 Python 함수에 직접 의존하지 않는다.
- dashboard는 replay artifact를 수정하지 않는다.

## Known Gaps

- 현재 smoke는 user 28, 30 events 기준의 작은 closed-loop 검증이다.
- 추천 scoring/evaluation은 아직 없다.
- replay는 batch마다 full active snapshot을 다시 assign/refit 후보로 읽으므로 `already_processed` record가 정상적으로 생긴다.
- `outputs/stream/replay_demo/`는 demo root 하나를 재사용한다. 여러 사람이 동시에 다른 실험을 돌릴 때는 `--output-root outputs/stream/replay_demo_<name>`처럼 별도 root를 쓰는 것이 안전하다.
- GPU backend는 환경 의존적이다. `auto`를 쓰면 가능한 경우 GPU를 쓰고, 현재 로컬처럼 CUDA runtime이 맞지 않으면 CPU fallback으로 진행한다.
