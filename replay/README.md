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

## Run Python Orchestrator

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --run-id phase5_replay_smoke \
  --input-events outputs/stream/replay_demo/replay_input_events.jsonl \
  --output-root outputs/stream/replay_demo \
  --micro-batch-size 20
```

The orchestrator writes replay progress, summary, and stream artifacts under `outputs/stream/replay_demo/`.

To build input events and run the closed-loop smoke in one command:

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
  --run-id phase5_replay_smoke
```

`--replay-speed 0` is the default and disables wall-clock pacing for quick demos. Set a positive value to compress timestamp gaps between micro-batches, capped by `--max-sleep-sec`.
