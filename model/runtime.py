from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging
import sys

import torch


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


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
