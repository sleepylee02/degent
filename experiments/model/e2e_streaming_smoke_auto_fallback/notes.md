# e2e_streaming_smoke_auto_fallback

## Purpose

- Verify that the replay closed-loop smoke completes on the current local environment when `--cluster-backend auto` cannot use CUDA successfully.

## Changes vs previous run

- `model.stream.cluster_refit` now probes the CUDA runtime before selecting GPU under `auto`.
- If an `auto` GPU refit fails at runtime, the refit retries once with the CPU `umap-learn + hdbscan` backend.

## Observations

- Replay status completed.
- Processed 30 / 30 replay events for user 28 in 2 micro-batches.
- Assignment records: 19.
- Refit requests opened / closed / skipped: 2 / 2 / 0.
- Backend selected by refit events: CPU fallback.
- Fallback reason: `cudaErrorInsufficientDriver`.
- Elapsed time: about 32.40 seconds.

## Next run

- Use this as the current successful local E2E smoke baseline when CUDA is visible but not usable by the installed runtime/driver combination.
- For a GPU-positive check, rerun on an environment where RAPIDS/cuML and the CUDA driver/runtime combination are compatible.
