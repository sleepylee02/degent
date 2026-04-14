# Model Experiments

This directory stores lightweight metadata for model runs.

Each run uses:

```text
experiments/model/<run_id>/
├── manifest.json
├── metrics.jsonl
└── notes.md
```

Heavy artifacts such as checkpoints, embeddings, and clustering outputs stay under `outputs/` and remain outside git tracking. The metadata records enough information to compare runs later: command, git state, input file metadata, schema versions, configs, output refs, and metrics.
