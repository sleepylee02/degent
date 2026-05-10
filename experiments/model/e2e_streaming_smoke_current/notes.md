# e2e_streaming_smoke_current

## Purpose

- Intermediate replay smoke used to reproduce the current local failure before adding the auto CPU fallback.

## Changes vs previous run

- This run used the pre-fix behavior and did not complete the full replay pipeline.

## Observations

- Metrics were recorded only through `extract_online` and `interest_assign`.
- No final `replay_pipeline` completed record exists in this run.
- The completed replacement run is `e2e_streaming_smoke_auto_fallback`.

## Next run

- Keep this run as failure context only.
- Use `e2e_streaming_smoke_auto_fallback` as the successful local smoke reference.
