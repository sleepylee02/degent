from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import hashlib
import json
import logging
import re
import shlex
import subprocess
import sys

import torch


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_HASH_LIMIT_BYTES = 100 * 1024 * 1024


def setup_run_logging(script_name: str, outputs_dir: Path) -> tuple[logging.Logger, Path]:
    logs_dir = outputs_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"{script_name}_{timestamp}.log"

    logger = logging.getLogger(f"model.{script_name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.info("Log file: %s", log_path)
    return logger, log_path


def local_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize_run_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._-") or "run"


def make_run_id(label: str = "sasrec_cl") -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{sanitize_run_id(label)}"


def latest_run_path(outputs_dir: Path) -> Path:
    return outputs_dir / "latest_model_run_id.txt"


def read_latest_run_id(outputs_dir: Path) -> str | None:
    path = latest_run_path(outputs_dir)
    if not path.exists():
        return None

    run_id = path.read_text(encoding="utf-8").strip()
    return run_id or None


def write_latest_run_id(outputs_dir: Path, run_id: str) -> None:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    latest_run_path(outputs_dir).write_text(f"{run_id}\n", encoding="utf-8")


def resolve_model_run_id(
    outputs_dir: Path,
    provided_run_id: str | None,
    *,
    prefer_latest: bool,
    label: str = "sasrec_cl",
) -> str:
    if provided_run_id:
        run_id = sanitize_run_id(provided_run_id)
    elif prefer_latest:
        run_id = read_latest_run_id(outputs_dir) or make_run_id(label)
    else:
        run_id = make_run_id(label)

    write_latest_run_id(outputs_dir, run_id)
    return run_id


def ensure_experiment_run(root: Path, run_id: str) -> Path:
    run_dir = root / "experiments" / "model" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    notes_path = run_dir / "notes.md"
    if not notes_path.exists():
        notes_path.write_text(
            "\n".join(
                [
                    f"# {run_id}",
                    "",
                    "## Purpose",
                    "",
                    "- TODO",
                    "",
                    "## Changes vs previous run",
                    "",
                    "- TODO",
                    "",
                    "## Observations",
                    "",
                    "- TODO",
                    "",
                    "## Next run",
                    "",
                    "- TODO",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    return run_dir


def command_line(argv: list[str] | None = None) -> str:
    return " ".join(shlex.quote(part) for part in (argv or sys.argv))


def _relative_path(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_metadata(
    path: Path,
    *,
    root: Path | None = None,
    include_sha256: bool = False,
    sha256_limit_bytes: int | None = DEFAULT_HASH_LIMIT_BYTES,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "path": _relative_path(path, root),
        "exists": path.exists(),
    }
    if not path.exists():
        return metadata

    stat = path.stat()
    metadata.update(
        {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    )

    if include_sha256:
        if sha256_limit_bytes is not None and stat.st_size > sha256_limit_bytes:
            metadata.update(
                {
                    "sha256": None,
                    "sha256_skipped": "size_exceeds_limit",
                    "sha256_limit_bytes": sha256_limit_bytes,
                }
            )
        else:
            metadata["sha256"] = sha256_file(path)

    return metadata


def read_schema_version(schema_path: Path) -> str | None:
    if not schema_path.exists():
        return None

    for line in schema_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def schema_metadata(schema_path: Path, *, root: Path | None = None) -> dict[str, Any]:
    metadata = file_metadata(schema_path, root=root, include_sha256=True)
    metadata["version"] = read_schema_version(schema_path)
    return metadata


def _git_output(root: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_metadata(root: Path) -> dict[str, Any]:
    status = _git_output(root, ["status", "--short"])
    return {
        "commit": _git_output(root, ["rev-parse", "HEAD"]),
        "branch": _git_output(root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": bool(status),
        "status_short": status.splitlines() if status else [],
    }


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def update_experiment_manifest(run_dir: Path, updates: dict[str, Any]) -> Path:
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"created_at": local_timestamp()}

    _deep_update(manifest, updates)
    manifest["updated_at"] = local_timestamp()

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def load_experiment_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def append_metric(run_dir: Path, record: dict[str, Any]) -> Path:
    metrics_path = run_dir / "metrics.jsonl"
    item = {"recorded_at": local_timestamp(), **record}
    with metrics_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return metrics_path


def set_global_seed(seed: int) -> None:
    import random

    import numpy as np

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def resolve_torch_device() -> tuple[torch.device, str]:
    if torch.cuda.is_available():
        device_index = torch.cuda.current_device()
        device_name = torch.cuda.get_device_name(device_index)
        return torch.device(f"cuda:{device_index}"), f"cuda:{device_index} ({device_name})"

    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return torch.device("mps"), "mps (Apple Metal)"

    return torch.device("cpu"), "cpu"


def log_torch_runtime(logger: logging.Logger, device: torch.device, device_label: str) -> None:
    logger.info("Selected torch device: %s", device_label)
    logger.info("PyTorch version: %s", torch.__version__)

    if device.type == "cuda":
        logger.info("CUDA device count: %d", torch.cuda.device_count())
        logger.info("CUDA current device index: %d", torch.cuda.current_device())
