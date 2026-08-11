#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from rram_cmodel.backends.destiny_runner import run_destiny_calibration


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Run pinned DESTINY and build a verified calibration manifest")
    p.add_argument("--destiny-dir", default=str(root / "third_party" / "destiny"))
    p.add_argument(
        "--config",
        default=str(root / "third_party" / "configs" / "destiny_rram_bank_1MiB_256b_4stack.cfg"),
    )
    p.add_argument("--output-dir", default=str(root / "calibration_runs" / "destiny_rram_4stack"))
    p.add_argument("--replicated-banks", type=int, default=64)
    args = p.parse_args()

    manifest = run_destiny_calibration(
        destiny_dir=args.destiny_dir,
        config_template=args.config,
        output_dir=args.output_dir,
        replicated_banks=args.replicated_banks,
    )
    print(manifest)


if __name__ == "__main__":
    main()
