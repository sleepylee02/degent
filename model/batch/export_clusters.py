#!/usr/bin/env python3
"""Export batch cluster results to dashboard table format."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_cluster_npz(path: Path) -> pd.DataFrame:
    data = np.load(path)
    required = {"labels_user_ids", "labels", "umap_z"}
    if not required.issubset(set(data.files)):
        missing = required - set(data.files)
        raise ValueError(f"Unsupported NPZ cluster format: missing {sorted(missing)}")

    user_ids = data["labels_user_ids"]
    labels = data["labels"]
    umap_z = data["umap_z"]

    if umap_z.ndim != 2 or umap_z.shape[1] < 2:
        raise ValueError("Unsupported umap_z format: expected shape (N, >=2)")

    frame = pd.DataFrame(
        {
            "userId": user_ids,
            "clusterLabel": labels,
            "x": umap_z[:, 0].astype(float),
            "y": umap_z[:, 1].astype(float),
        }
    )

    if umap_z.shape[1] >= 3:
        frame["z"] = umap_z[:, 2].astype(float)

    if "labels_timepoints" in data.files:
        frame["timepoint"] = data["labels_timepoints"].astype(int)

    if "labels_user_ids" in data.files:
        frame["sequenceLength"] = frame.groupby("userId")["userId"].transform("count")

    return frame


def write_frame(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()
    if suffix == ".parquet":
        frame.to_parquet(output_path, index=False)
    elif suffix == ".csv":
        frame.to_csv(output_path, index=False)
    elif suffix in {".jsonl", ".ndjson"}:
        with output_path.open("w", encoding="utf-8") as handle:
            for record in frame.to_dict(orient="records"):
                handle.write(f"{json.dumps(record, ensure_ascii=False)}\n")
    else:
        raise ValueError(f"Unsupported output format: {suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export batch cluster NPZ results to dashboard table format."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("outputs/user_interests.npz"),
        help="Source batch cluster NPZ file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/clustering/user_clusters.parquet"),
        help="Destination dashboard table path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame = load_cluster_npz(args.input)
    write_frame(frame, args.output)
    print(f"Exported cluster table: {args.output} ({len(frame)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
