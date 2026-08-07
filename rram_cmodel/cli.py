from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path

from .presets import functional_example
from .simulator import CycleEventSimulator
from .workload import GemmWorkload


def _workload_from_args(args) -> GemmWorkload:
    return GemmWorkload(
        M=args.M,
        K=args.K,
        N=args.N,
        weight_bits=args.weight_bits,
        activation_bits=args.activation_bits,
    )


def cmd_compare(args) -> None:
    cfg = functional_example()
    cfg = replace(cfg, pe=replace(cfg.pe, psum_contexts=max(cfg.pe.psum_contexts, args.M)))
    sim = CycleEventSimulator(cfg)
    out = sim.compare(_workload_from_args(args))
    print(json.dumps(out, indent=2, sort_keys=True))


def cmd_dse(args) -> None:
    base = functional_example()
    workload = _workload_from_args(args)
    rows = []
    for banks in args.rram_banks:
        for tsv_width in args.tsv_width:
            for wbuf_kb in args.wbuf_kb:
                cfg = replace(
                    base,
                    pe=replace(base.pe, psum_contexts=max(base.pe.psum_contexts, workload.M)),
                    rram=replace(base.rram, num_banks=banks),
                    tsv=replace(base.tsv, width_bits_per_cycle=tsv_width),
                    wbuf=replace(base.wbuf, capacity_bytes=wbuf_kb * 1024),
                )
                out = CycleEventSimulator(cfg).compare(workload)
                rows.append({
                    "M": workload.M,
                    "K": workload.K,
                    "N": workload.N,
                    "weight_bits": workload.weight_bits,
                    "rram_banks": banks,
                    "tsv_width_bits": tsv_width,
                    "wbuf_kb": wbuf_kb,
                    "nmp_cycles": out["rram_nmp"]["cycles"],
                    "npu_direct_cycles": out["npu_direct"]["cycles"],
                    "npu_hier_cycles": out["npu_hierarchical"]["cycles"],
                    "nmp_pe_util": out["rram_nmp"]["pe_utilization"],
                    "nmp_weight_stall": out["rram_nmp"]["stall_cycles_weight"],
                    "speedup_vs_direct": out["speedup_vs_direct"],
                    "speedup_vs_hier": out["speedup_vs_hierarchical"],
                })
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(path)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RRAM logic-die PIM cycle/event CModel")
    sub = p.add_subparsers(required=True)

    def add_workload(sp):
        sp.add_argument("--M", type=int, required=True)
        sp.add_argument("--K", type=int, required=True)
        sp.add_argument("--N", type=int, required=True)
        sp.add_argument("--weight-bits", type=int, default=8)
        sp.add_argument("--activation-bits", type=int, default=8)

    c = sub.add_parser("compare")
    add_workload(c)
    c.set_defaults(func=cmd_compare)

    d = sub.add_parser("dse")
    add_workload(d)
    d.add_argument("--rram-banks", type=int, nargs="+", default=[8, 16, 32, 64, 128])
    d.add_argument("--tsv-width", type=int, nargs="+", default=[128, 256, 512, 1024])
    d.add_argument("--wbuf-kb", type=int, nargs="+", default=[8, 16, 32, 64, 128])
    d.add_argument("--output", default="results/dse.csv")
    d.set_defaults(func=cmd_dse)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
