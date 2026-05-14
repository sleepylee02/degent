# replay/

Timestamp replay utilities for the streaming demo pipeline.

## Build

```bash
make -C replay
```

## Generate Replay Events

```bash
replay/bin/rating_replay \
  --input data/ratings_drop_processed.jsonl \
  --output outputs/stream/replay_demo/replay_input_events.jsonl \
  --user-id 28 \
  --limit-events 100
```

The generated JSONL follows `docs/streaming-replay-dashboard-contract.md`.

## Run Trace Replay

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --run-id trace_replay_smoke \
  --input-events outputs/stream/replay_demo/replay_input_events.jsonl \
  --output-root outputs/stream/replay_demo \
  --speed 100
```

The runner replays each event according to:

```text
scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / speed
```

It writes emitted ingress events, replay progress, lag/throughput summary, and stream artifacts under `outputs/stream/replay_demo/`.

Add `--recommend` to run online recommendation after each emitted event. Recommendation rows are appended to `outputs/stream/replay_demo/stream_recommendations.jsonl` and exposed through the replay summary `paths`.

To build input events and run the closed-loop smoke in one command:

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

Use a larger `--speed` for faster trace replay. The runner records `scheduledAt`, `emittedAt`, `injectorLagSec`, `processingLagSec`, `endToEndLagSec`, target event rate, actual throughput, and `behindScheduleEvents` so N-speed replay quality is measurable.
