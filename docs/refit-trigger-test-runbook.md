# Refit Trigger Test Runbook

This note documents special replay modes for testing refit trigger behavior.
Use the normal replay defaults for production-like runs.

## Normal Trigger Behavior

The default refit trigger is assignment-state based and per user:

- `no_interest_pending_events`: no interest state exists and pending active embedding rows reach `--refit-min-events`.
- `pending_events`: interest state exists and pending active embedding rows reach `--refit-min-events`.
- `assigned_since_last_refit`: assigned active embedding rows since last refit reach `--assign-trigger-count`.
- `outlier_since_last_refit`: outlier active embedding rows since last refit reach `--outlier-trigger-count`.

These triggers count active positive embedding rows, not raw replay input rows.

## Raw Incoming Event Cadence Test

For brute-force testing, `model.stream.replay_pipeline` now has two replay-only flags:

- `--force-refit-every-user-events N`: opens a refit request every `N` raw replay events for the same user.
- `--disable-assignment-refit-triggers`: prevents `interest_assign` from opening its normal assignment-state refit requests, so the run isolates raw-event cadence.

With `--force-refit-every-user-events 10`, each user opens a forced refit request at their 10th, 20th, 30th, ... incoming raw replay event.

Important: this only forces request opening. `cluster_refit` still needs active embedding rows to cluster. If there are too few active embeddings, the request is skipped according to `--refit-min-events`.

## Example Command

```bash
.venv/bin/python -m model.stream.replay_pipeline \
  --run-id refit_every10_raw_smoke \
  --output-root outputs/post/refit_every10_raw_smoke \
  --reset-output \
  --generate-events \
  --start-rated-at 2022-01-01T00:00:00Z \
  --limit-events 500 \
  --speed 1000 \
  --checkpoint outputs/pre/temporal_2022/sasrec_cl.pt \
  --item2idx outputs/pre/temporal_2022/item2idx.json \
  --seed-state-db outputs/pre/temporal_2022/state.sqlite \
  --seed-run-id temporal_2022 \
  --force-refit-every-user-events 10 \
  --disable-assignment-refit-triggers \
  --refit-min-events 1 \
  --min-cluster-size 5 \
  --cluster-dim 5 \
  --cluster-backend cpu
```

Use `--cluster-backend auto` for a GPU-first run after the CPU smoke is confirmed.

## Checks

After the run, check forced request counts:

```bash
.venv/bin/python - <<'PY'
import json
from collections import Counter
from pathlib import Path

path = Path("outputs/post/refit_every10_raw_smoke/production/refit_requests.jsonl")
reasons = Counter()
forced_counts = []
for line in path.read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    reasons.update(row.get("reasons", []))
    if "forced_raw_user_event_interval" in row.get("reasons", []):
        forced_counts.append(row.get("rawUserEventCount"))

print("reasons:", dict(reasons))
print("forced raw user event counts:", forced_counts[:20])
PY
```

Expected forced requests have:

- `reasons: ["forced_raw_user_event_interval"]`
- `rawUserEventCount` equal to `10`, `20`, `30`, ...
- `forceRefitEveryUserEvents: 10`

The replay summary also records:

- `totals.forceRefitEveryUserEvents`
- `totals.forcedRefitRequestsOpened`
- `totals.assignmentRefitTriggersDisabled`

