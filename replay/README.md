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
  --output outputs/post/replay_demo/replay_input_events.jsonl \
  --user-id 28 \
  --limit-events 100
```

The generated JSONL follows `docs/streaming-replay-dashboard-contract.md`.

## Run Trace Replay

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --run-id trace_replay_smoke \
  --input-events outputs/post/replay_demo/replay_input_events.jsonl \
  --output-root outputs/post/replay_demo \
  --speed 100
```

The runner replays each event according to:

```text
scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / speed
```

It writes emitted ingress events, replay progress, lag/throughput summary, and replay artifacts under `outputs/post/replay_demo/`.

Add `--recommend` to run online recommendation after each emitted event. Recommendation rows are appended to `outputs/post/replay_demo/stream_recommendations.jsonl` and exposed through the replay summary `paths`.

For temporal E2E runs, generate only post-cutoff events and lazy-load replay state from the pre-cutoff SQLite seed store:

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --run-id temporal_2022_replay_events_1000 \
  --output-root outputs/post/temporal_2022_events_1000 \
  --reset-output \
  --generate-events \
  --start-rated-at 2022-01-01T00:00:00Z \
  --limit-events 1000 \
  --speed 100 \
  --checkpoint outputs/pre/temporal_2022/sasrec_cl.pt \
  --item2idx outputs/pre/temporal_2022/item2idx.json \
  --seed-state-db outputs/pre/temporal_2022/state.sqlite \
  --seed-run-id temporal_2022
```

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
