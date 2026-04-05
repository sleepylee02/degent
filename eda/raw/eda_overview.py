#!/usr/bin/env python3

from __future__ import annotations

import argparse

from eda.raw.combined import run as run_combined
from eda.raw.genome2021 import run as run_genome2021
from eda.raw.ml32m import run as run_ml32m


def main() -> None:
    parser = argparse.ArgumentParser(description="Run separated EDA for MovieLens and Genome 2021, then merge summaries.")
    parser.add_argument(
        "--source",
        choices=["all", "ml32m", "genome2021", "combined"],
        default="all",
        help="Which analysis to run. 'combined' reruns both source analyses before writing the merged summary.",
    )
    args = parser.parse_args()

    if args.source == "ml32m":
        ml_summary = run_ml32m()
        print(f"[done] MovieLens outputs written to {ml_summary['output_dir']}", flush=True)
        return

    if args.source == "genome2021":
        genome_summary = run_genome2021()
        print(f"[done] Genome 2021 outputs written to {genome_summary['output_dir']}", flush=True)
        return

    ml_summary = run_ml32m()
    genome_summary = run_genome2021()
    combined_summary = run_combined(ml_summary, genome_summary)

    print(f"[done] MovieLens outputs written to {ml_summary['output_dir']}", flush=True)
    print(f"[done] Genome 2021 outputs written to {genome_summary['output_dir']}", flush=True)
    print(f"[done] Combined report written to {combined_summary['eda_report_path']}", flush=True)


if __name__ == "__main__":
    main()
