# Streaming Replay/Dashboard Contract

이 문서는 Phase 5 replay engine과 Phase 6 dashboard가 병렬로 구현될 수 있도록 고정하는 파일 기반 인터페이스 계약이다.

## Version

`stream_replay_demo.v1`

## Ownership

- Phase 5 owns writes under `outputs/stream/replay_demo/`.
- Phase 6 reads `outputs/stream/replay_demo/` and must not depend on Phase 5 internal functions, process model, CLI implementation, or C++ code structure.
- Contract changes must be made here first, then reflected in Phase 5 and Phase 6 plans. Do not silently change dashboard expectations from Phase 6 only.

## Default Root

All demo artifacts live under:

```text
outputs/stream/replay_demo/
```

Phase 5 must not overwrite the existing default Phase 3~4-1 artifacts:

```text
outputs/stream/online_embeddings.npz
outputs/stream/user_states/
outputs/stream/interest_states/
outputs/stream/interest_assignments.jsonl
outputs/stream/refit_requests.jsonl
outputs/stream/refit_events.jsonl
```

## Phase 5 Outputs

```text
outputs/stream/replay_demo/replay_input_events.jsonl
outputs/stream/replay_demo/replay_events.jsonl
outputs/stream/replay_demo/replay_summary.json
outputs/stream/replay_demo/user_states/{user_id}.json
outputs/stream/replay_demo/interest_states/{user_id}.json
outputs/stream/replay_demo/online_embeddings.npz
outputs/stream/replay_demo/online_embedding_events.jsonl
outputs/stream/replay_demo/interest_assignments.jsonl
outputs/stream/replay_demo/refit_requests.jsonl
outputs/stream/replay_demo/refit_events.jsonl
```

## Replay Input Event JSONL

`replay_input_events.jsonl` is produced by the replay event generator and consumed by the Python replay orchestrator.

Required fields per line:

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

Ordering contract:

- Sort by `ratedAtTs`, then `userId`, then `movieId`, then `eventId`.
- `eventId` is globally unique within the replay input file.
- `replayOrder` is the 0-based line/order after sorting.

## Replay Events JSONL

`replay_events.jsonl` is an append-only progress log written by Phase 5 and read by Phase 6.

Required fields per line:

```json
{
  "version": "stream_replay_progress.v1",
  "recordedAt": "2026-05-06T13:00:00+09:00",
  "runId": "phase5_replay_smoke",
  "stage": "micro_batch",
  "status": "completed",
  "batchId": 0,
  "eventStart": 0,
  "eventEnd": 19,
  "processedEvents": 20,
  "uniqueUsers": 1,
  "activeEmbeddingRows": 12,
  "assignmentStatusCounts": {
    "assigned": 0,
    "pending_no_interest": 12
  },
  "refitRequestsOpened": 0,
  "refitClosed": 0,
  "latencySec": 1.23
}
```

Allowed `stage` values:

- `start`
- `micro_batch`
- `refit`
- `end`
- `error`

Allowed `status` values:

- `started`
- `completed`
- `skipped`
- `failed`

Phase 6 must tolerate missing optional metrics and render available fields.

Optional replay clock fields:

- `replayClockTs`: latest replay input timestamp covered by the micro-batch.
- `replayClockRatedAt`: ISO timestamp for `replayClockTs`.
- `replaySleepSec`: wall-clock sleep inserted before the micro-batch when replay pacing is enabled.

## Replay Summary JSON

`replay_summary.json` is the stable summary entrypoint for Phase 6.

Required fields:

```json
{
  "version": "stream_replay_summary.v1",
  "runId": "phase5_replay_smoke",
  "status": "completed",
  "startedAt": "2026-05-06T13:00:00+09:00",
  "endedAt": "2026-05-06T13:01:00+09:00",
  "elapsedSec": 60.0,
  "inputEvents": 100,
  "processedEvents": 100,
  "uniqueUsers": 1,
  "microBatchSize": 20,
  "refitBackend": "auto",
  "totals": {
    "activeEmbeddingRows": 100,
    "assignmentRecords": 100,
    "refitRequestsOpened": 1,
    "refitClosed": 1,
    "refitSkipped": 0
  },
  "paths": {
    "replayEvents": "outputs/stream/replay_demo/replay_events.jsonl",
    "onlineEmbeddings": "outputs/stream/replay_demo/online_embeddings.npz",
    "interestAssignments": "outputs/stream/replay_demo/interest_assignments.jsonl",
    "refitRequests": "outputs/stream/replay_demo/refit_requests.jsonl",
    "refitEvents": "outputs/stream/replay_demo/refit_events.jsonl",
    "interestStateDir": "outputs/stream/replay_demo/interest_states"
  }
}
```

Phase 6 should use `paths` from this file when present and fall back to the default paths above.

## Phase 6 Read Scope

Phase 6 may read:

- `replay_summary.json`
- `replay_events.jsonl`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`
- `refit_events.jsonl`
- `interest_states/{user_id}.json`

Phase 6 must not mutate these files.

## Non-Goals

- No recommendation scoring/evaluation.
- No dashboard-driven mutation of stream state.
- No direct editing of generated replay artifacts.
