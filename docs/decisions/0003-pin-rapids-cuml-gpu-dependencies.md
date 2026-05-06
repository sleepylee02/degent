# RAPIDS cuML GPU Dependency Versioning

## Status

Accepted

## Context

Streaming Phase 4-1 adds `model.stream.cluster_refit`, which can refit user interest vectors with RAPIDS cuML UMAP/HDBSCAN when a local GPU is available.

The local machine has an RTX 3090 and NVIDIA driver `535.288.01`, with `nvidia-smi` reporting CUDA `12.2`. The existing model environment already used `torch==2.5.1+cu121`.

Installing `cuml-cu12==26.4.0` pulled CUDA `12.9` runtime wheels. Imports succeeded, but a basic CuPy kernel and cuML UMAP failed with `CUDA_ERROR_INVALID_IMAGE`. It also conflicted with PyTorch's exact `cu121` NVIDIA wheel requirements.

## Decision

Keep all Python dependency changes inside the repo-local `.venv`; do not install or change system-level Python, CUDA, driver, or OS packages for this project.

Pin the project GPU stack to the tested local combination:

- `torch==2.5.1+cu121`
- RAPIDS/cuML `25.10.0` family: `cuml-cu12==25.10.0`, `cudf-cu12==25.10.0`, `rmm-cu12==25.10.0`, related RAPIDS `25.10.0` wheels
- `cuda-toolkit==12.1.1`
- PyTorch `cu121` NVIDIA wheels such as `nvidia-cublas-cu12==12.1.3.1`, `nvidia-cuda-runtime-cu12==12.1.105`, `nvidia-cusolver-cu12==11.4.5.107`
- `cupy-cuda12x==13.6.0`
- `scikit-learn==1.7.2`

`requirements.txt` preserves both package indexes:

- `https://download.pytorch.org/whl/cu121`
- `https://pypi.nvidia.com`

Do not upgrade to RAPIDS/cuML `26.x` until driver/runtime compatibility is intentionally revisited and smoke-tested.

## Consequences

`--cluster-backend auto` now selects GPU on the local `.venv` and Phase 4-1 GPU smoke passes.

The final smoke on 2026-05-06 used `phase4_1_cluster_refit_auto_gpu_smoke_final` and produced:

- backend selected: `gpu`
- active embedding rows: `1579`
- interest count: `33`
- processed raw events: `1579`
- refit elapsed: about `0.26s`

Dependency updates must continue to run through `.venv/bin/pip` and must be followed by:

- `.venv/bin/pip check`
- a PyTorch CUDA import check
- a CuPy CUDA kernel check
- a cuML/cudf import check
- `python3 -m model.stream.cluster_refit` GPU or `auto` smoke
