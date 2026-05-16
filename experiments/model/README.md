# Model Experiments

This directory stores local metadata for model runs. This README is tracked to document the structure; individual run directories are ignored by git.

Each run uses:

```text
experiments/model/<run_id>/
├── manifest.json
├── metrics.jsonl
└── notes.md
```

Heavy artifacts such as checkpoints, embeddings, and clustering outputs stay under `outputs/` and remain outside git tracking. The local metadata records enough information to compare runs later on the same machine: command, git state, input file metadata, schema versions, configs, output refs, and metrics.

Use this directory for run-level history, not for copied source snapshots.

- `manifest.json`: command, git state, input/output metadata, schema versions, config, output refs
- `metrics.jsonl`: training metrics plus extract/cluster/refit/recommend/replay summaries
- `notes.md`: run purpose, changes vs previous run, observations, follow-up questions

Model code history is tracked by git. Design decisions that should remain visible to humans and LLMs are recorded in `docs/decisions/`.
