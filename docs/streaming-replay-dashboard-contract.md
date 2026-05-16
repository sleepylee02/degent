# Streaming Replay/Dashboard Contract

이 문서는 trace-clock replay runner와 dashboard가 공유하는 replay artifact 인터페이스 계약이다. 현재 우선 경로는 SQLite runtime store이며, 기존 JSONL/JSON 파일은 fallback/debug로 유지한다. Post replay의 active online embedding은 SQLite cache가 정본이고, `online_embeddings.npz`는 명시적으로 export할 때만 생성하는 debug artifact다.

## Version

`stream_trace_replay_demo.v1`

## Ownership

- Trace replay owns writes under `outputs/post/replay_demo/`.
- Dashboard reads `outputs/post/replay_demo/` and must not depend on trace replay internal functions, process model, CLI implementation, or C++ code structure.
- When `replay_summary.json` contains `paths.replayDb`, dashboard must prefer SQLite runtime store reads and fall back to JSONL/JSON artifacts only when the DB is absent or unreadable.
- Contract changes must be made here first, then reflected in runner and dashboard docs. Do not silently change dashboard expectations from dashboard code only.

## Default Root

All demo artifacts live under:

```text
outputs/post/replay_demo/
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

## Trace Replay Outputs

```text
outputs/post/replay_demo/replay_input_events.jsonl
outputs/post/replay_demo/ingress_events.jsonl
outputs/post/replay_demo/replay_events.jsonl
outputs/post/replay_demo/replay.sqlite
outputs/post/replay_demo/replay_summary.json
outputs/post/replay_demo/user_states/{user_id}.json
outputs/post/replay_demo/interest_states/{user_id}.json
outputs/post/replay_demo/online_embeddings.npz  # optional: --export-online-embeddings-npz
outputs/post/replay_demo/online_embedding_events.jsonl
outputs/post/replay_demo/interest_assignments.jsonl
outputs/post/replay_demo/refit_requests.jsonl
outputs/post/replay_demo/refit_events.jsonl
outputs/post/replay_demo/stream_recommendations.jsonl
```

## SQLite Runtime Store

`replay.sqlite` is the runtime/state/control-plane store for a replay run. Post replay active online embeddings are stored in SQLite `active_embedding_cache`; per-event delta assignment reads `embedding_cache_changes`. Large batch matrices such as canonical embeddings, model checkpoints, and batch outputs remain file-backed. `online_embeddings.npz` is optional debug/export output when `--export-online-embeddings-npz` is passed.

Dashboard-readable tables:

| table | role |
|---|---|
| `runs` | run status, speed, output root, summary path |
| `input_events` | replay input event identity and original payload |
| `event_progress` | event schedule/emit/process timestamps and lag metrics |
| `stage_attempts` | per-stage status, latency, command, failure metadata |
| `runtime_metrics` | queue/backlog/throughput snapshots |
| `user_states` | user state payload and raw/positive counts |
| `user_raw_events` | raw rating events by user |
| `user_positive_events` | current positive projection rows by user |
| `active_embedding_cache` | latest active online embedding rows by run/user/raw event |
| `embedding_cache_changes` | per-event insert/update/inactivate signal for active embedding cache |
| `interest_states` | interest state payload, pending/processed/refit flags |
| `interest_vectors` | small interest vectors as `float32` BLOBs |
| `assignments` | assignment/pending/outlier/already-processed records |
| `refit_requests` | refit lifecycle: `open`, `running`, `closed`, `skipped`, `failed`, `superseded` |
| `refit_attempts` | refit attempt result, backend, skip/error metadata |
| `embedding_snapshots` | file-backed embedding snapshot metadata |
| `embedding_rows` | snapshot row index for user/raw event/movie rows |
| `recommendation_runs` | recommendation run metadata |
| `recommendation_rows` | top-K recommendation rows |

Runtime DB writes use SQLite WAL mode and a busy timeout so dashboard reads can occur while replay is running. Dashboard is still read-only and must not mutate this DB.

`model.stream.runtime_report` is a read-only consumer of the same DB. It may be used to generate markdown/json bottleneck summaries, but report output is not a required dashboard input.

## Replay Input Event JSONL

`replay_input_events.jsonl` is produced by the replay event generator and consumed by the Python trace replay runner.

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

## Ingress Events JSONL

`ingress_events.jsonl` is an append-only log of events emitted by the trace scheduler. One record means the trace runner attempted to inject one event at its scheduled wall-clock time.

Required fields per line:

```json
{
  "version": "stream_ingress_event.v1",
  "recordedAt": "2026-05-14T17:05:16.439686+09:00",
  "runId": "trace_replay_smoke",
  "eventId": 0,
  "replayOrder": 0,
  "userId": 28,
  "movieId": 839,
  "rating": 1.0,
  "ratedAt": "2000-06-19T17:36:56Z",
  "ratedAtTs": 961436216.0,
  "source": "ratings_drop_processed",
  "speed": 100.0,
  "scheduledAt": "2026-05-14T17:05:16.439442+09:00",
  "emittedAt": "2026-05-14T17:05:16.439686+09:00",
  "injectorLagSec": 0.0002,
  "behindSchedule": false
}
```

The schedule contract is:

```text
scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / speed
```

## Replay Events JSONL

`replay_events.jsonl` is an append-only progress log written by trace replay and read by dashboard.

Required fields per line:

```json
{
  "version": "stream_trace_replay_progress.v1",
  "recordedAt": "2026-05-14T17:05:23.379422+09:00",
  "runId": "trace_replay_smoke",
  "stage": "trace_event",
  "status": "completed",
  "eventOrdinal": 0,
  "eventId": 0,
  "replayOrder": 0,
  "userId": 28,
  "movieId": 839,
  "ratedAt": "2000-06-19T17:36:56Z",
  "ratedAtTs": 961436216.0,
  "speed": 100.0,
  "scheduledAt": "2026-05-14T17:05:16.439442+09:00",
  "emittedAt": "2026-05-14T17:05:16.439686+09:00",
  "processingStartedAt": "2026-05-14T17:05:16.439791+09:00",
  "processedAt": "2026-05-14T17:05:23.379422+09:00",
  "injectorLagSec": 0.0002,
  "processingLagSec": 6.94,
  "endToEndLagSec": 6.94,
  "behindSchedule": false,
  "queueDepth": 0,
  "processedEvents": 1,
  "uniqueUsers": 1,
  "activeEmbeddingRows": 1,
  "assignmentStatusCounts": {
    "pending_no_interest": 1
  },
  "refitRequestsOpened": 0,
  "refitClosed": 0,
  "latencySec": 6.94
}
```

Allowed `stage` values:

- `start`
- `trace_event`
- `end`
- `error`

Allowed `status` values:

- `started`
- `completed`
- `skipped`
- `failed`

Phase 6 must tolerate missing optional metrics and render available fields.

Trace replay fields:

- `scheduledAt`: target wall-clock time calculated from trace time and `speed`.
- `emittedAt`: wall-clock time when the event was emitted into the replay run.
- `processingStartedAt`: wall-clock time before online processing starts.
- `processedAt`: wall-clock time after online processing completes.
- `injectorLagSec`: `emittedAt - scheduledAt`, clipped at zero.
- `processingLagSec`: `processedAt - emittedAt`, clipped at zero.
- `endToEndLagSec`: `processedAt - scheduledAt`, clipped at zero.
- `behindSchedule`: true when the event could not be emitted on schedule.
- `queueDepth`: reserved for future producer/consumer split. The current runner records `0`.

## Replay Summary JSON

`replay_summary.json` is the stable summary entrypoint for Phase 6.

When `paths.replayDb` exists, dashboard must prefer SQLite runtime store reads for replay monitor tables. JSONL files remain fallback/debug artifacts.

Required fields:

```json
{
  "version": "stream_trace_replay_summary.v1",
  "runId": "trace_replay_smoke",
  "status": "completed",
  "startedAt": "2026-05-14T17:05:16+09:00",
  "endedAt": "2026-05-14T17:05:48+09:00",
  "elapsedSec": 31.74,
  "inputEvents": 5,
  "processedEvents": 5,
  "uniqueUsers": 1,
  "speed": 100.0,
  "traceStartTs": 961436216.0,
  "traceEndTs": 961436248.0,
  "traceSpanSec": 32.0,
  "scheduledSpanSec": 0.32,
  "throughputEventsPerSec": 0.157,
  "targetEventsPerSec": 15.625,
  "refitBackend": "cpu",
  "totals": {
    "activeEmbeddingRows": 10,
    "assignmentRecords": 10,
    "refitRequestsOpened": 1,
    "refitClosed": 0,
    "refitSkipped": 0,
    "recommendationRows": 0,
    "behindScheduleEvents": 4,
    "maxInjectorLagSec": 25.26,
    "meanInjectorLagSec": 12.89,
    "maxProcessingLagSec": 6.94,
    "meanProcessingLagSec": 6.35,
    "maxEndToEndLagSec": 31.42,
    "meanEndToEndLagSec": 19.24
  },
  "paths": {
    "replayInputEvents": "outputs/post/replay_demo/replay_input_events.jsonl",
    "ingressEvents": "outputs/post/replay_demo/ingress_events.jsonl",
    "replayEvents": "outputs/post/replay_demo/replay_events.jsonl",
    "replayDb": "outputs/post/replay_demo/replay.sqlite",
    "onlineEmbeddings": null,
    "interestAssignments": "outputs/post/replay_demo/interest_assignments.jsonl",
    "refitRequests": "outputs/post/replay_demo/refit_requests.jsonl",
    "refitEvents": "outputs/post/replay_demo/refit_events.jsonl",
    "interestStateDir": "outputs/post/replay_demo/interest_states",
    "streamRecommendations": "outputs/post/replay_demo/stream_recommendations.jsonl"
  }
}
```

Phase 6 should use `paths` from this file when present and fall back to the default paths above. `paths.onlineEmbeddings` may be `null` in the default post replay path.

## Stream Recommendations JSONL

`stream_recommendations.jsonl` is optional. It is written only when trace replay runs with recommendation enabled.

Fields currently written per line:

```json
{
  "recordedAt": "2026-05-06T13:00:00+09:00",
  "runId": "replay_with_recommend",
  "userId": 28,
  "rank": 1,
  "movieId": 2571,
  "itemIdx": 1234,
  "title": "The Matrix",
  "genres": "[\"Action\", \"Sci-Fi\"]",
  "score": 12.34,
  "bestClusterId": 3,
  "clusterScores": [12.34, 8.76],
  "normalize": false,
  "includeSeen": false,
  "topK": 20
}
```

Phase 6 should tolerate either `movieId` or `recommendedMovieId` as the recommended item field. If the file is absent, recommendation panels should render an empty state without failing the replay monitor.

## Phase 6 Read Scope

Phase 6 may read:

- `replay.sqlite`
- `replay_summary.json`
- `ingress_events.jsonl`
- `replay_events.jsonl`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`
- `refit_events.jsonl`
- `interest_states/{user_id}.json`
- `stream_recommendations.jsonl`

Phase 6 must not mutate these files.

## Non-Goals

- No offline recommendation evaluation metrics.
- No dashboard-driven mutation of stream state.
- No direct editing of generated replay artifacts.
- No Kafka/Flink/Postgres/Redis/Kubernetes/vector DB requirement for this local replay contract.
