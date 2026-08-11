from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from .backends.destiny import DestinyManifestBuilder
from .backends.ramulator21 import Ramulator21HBM3Adapter, Ramulator21HBM3Config
from .mapping import GemmMapper
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


def _configured_base(args, workload: GemmWorkload):
    cfg = functional_example()
    if getattr(args, "destiny_manifest", None):
        cfg = DestinyManifestBuilder.load_and_apply(args.destiny_manifest, cfg)
    return replace(
        cfg,
        pe=replace(cfg.pe, psum_contexts=max(cfg.pe.psum_contexts, workload.M)),
    )


def _weight_trace_segments(workload: GemmWorkload, cfg):
    mapper = GemmMapper(workload, cfg.pe, cfg.wbuf)
    segments = []
    for tile in mapper.tiles():
        segments.extend(tile.weight_segments(workload))
    return segments


def cmd_compare(args) -> None:
    workload = _workload_from_args(args)
    cfg = _configured_base(args, workload)
    sim = CycleEventSimulator(cfg)
    out = sim.compare(workload)
    out["config_metadata"] = dict(cfg.metadata)
    print(json.dumps(out, indent=2, sort_keys=True))


def cmd_dse(args) -> None:
    workload = _workload_from_args(args)
    base = _configured_base(args, workload)
    rows = []
    for banks in args.rram_banks:
        for tsv_width in args.tsv_width:
            for fifo_depth in args.tsv_fifo_depth:
                for wbuf_banks in args.wbuf_banks:
                    for wbuf_kb in args.wbuf_kb:
                        cfg = replace(
                            base,
                            rram=replace(base.rram, num_banks=banks),
                            tsv=replace(base.tsv, width_bits_per_cycle=tsv_width),
                            tsv_fifo=replace(base.tsv_fifo, depth_entries=fifo_depth),
                            wbuf=replace(
                                base.wbuf,
                                capacity_bytes=wbuf_kb * 1024,
                                banking=replace(base.wbuf.banking, num_banks=wbuf_banks),
                            ),
                        )
                        out = CycleEventSimulator(cfg).compare(workload)
                        nmp = out["rram_nmp"]
                        rows.append({
                            "M": workload.M,
                            "K": workload.K,
                            "N": workload.N,
                            "weight_bits": workload.weight_bits,
                            "rram_banks": banks,
                            "tsv_width_bits": tsv_width,
                            "tsv_fifo_depth": fifo_depth,
                            "wbuf_banks": wbuf_banks,
                            "wbuf_kb": wbuf_kb,
                            "nmp_cycles": nmp["cycles"],
                            "npu_direct_cycles": out["npu_direct"]["cycles"],
                            "npu_hier_cycles": out["npu_hierarchical"]["cycles"],
                            "nmp_pe_util": nmp["pe_utilization"],
                            "nmp_weight_stall": nmp["stall_cycles_weight"],
                            "nmp_tsv_queue_stall": nmp["queue_stall_cycles"].get("tsv", 0),
                            "nmp_tsv_fifo_max_occupancy": nmp["max_queue_occupancy"].get("tsv", 0),
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


def cmd_hbm_trace(args) -> None:
    workload = _workload_from_args(args)
    cfg = _configured_base(args, workload)
    segments = _weight_trace_segments(workload, cfg)
    hbm_cfg = Ramulator21HBM3Config(
        org_preset=args.hbm_org,
        timing_preset=args.hbm_timing,
        num_channels=args.hbm_channels,
        frontend_clock_ratio=args.frontend_clock_ratio,
        memory_clock_ratio=args.memory_clock_ratio,
        scheduler=args.scheduler,
        row_policy=args.row_policy,
        addr_mapper=args.addr_mapper,
        refresh_manager=args.refresh_manager,
        transaction_bytes=args.transaction_bytes,
    )
    adapter = Ramulator21HBM3Adapter(hbm_cfg, python_executable=args.python)
    result = adapter.run_segments(segments, args.workdir, stem=args.stem)
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RRAM logic-die PIM cycle/event CModel")
    sub = p.add_subparsers(required=True)

    def add_workload(sp):
        sp.add_argument("--M", type=int, required=True)
        sp.add_argument("--K", type=int, required=True)
        sp.add_argument("--N", type=int, required=True)
        sp.add_argument("--weight-bits", type=int, default=8)
        sp.add_argument("--activation-bits", type=int, default=8)
        sp.add_argument(
            "--destiny-manifest",
            default=None,
            help="Optional immutable DESTINY calibration manifest for RRAM/TSV physical parameters",
        )

    c = sub.add_parser("compare")
    add_workload(c)
    c.set_defaults(func=cmd_compare)

    d = sub.add_parser("dse")
    add_workload(d)
    d.add_argument("--rram-banks", type=int, nargs="+", default=[8, 16, 32, 64, 128])
    d.add_argument("--tsv-width", type=int, nargs="+", default=[128, 256, 512, 1024])
    d.add_argument("--tsv-fifo-depth", type=int, nargs="+", default=[2, 4, 8, 16])
    d.add_argument("--wbuf-banks", type=int, nargs="+", default=[8, 16, 32])
    d.add_argument("--wbuf-kb", type=int, nargs="+", default=[8, 16, 32, 64, 128])
    d.add_argument("--output", default="results/dse.csv")
    d.set_defaults(func=cmd_dse)

    h = sub.add_parser(
        "hbm-trace",
        help="Run the exact weight-address trace through official Ramulator2.1 HBM3",
    )
    add_workload(h)
    h.add_argument("--workdir", default="results/ramulator21")
    h.add_argument("--stem", default="hbm3_weight_trace")
    h.add_argument("--python", default=sys.executable)
    h.add_argument("--hbm-org", default="HBM3_8Gb_8hi")
    h.add_argument("--hbm-timing", default="HBM3_6400Mbps")
    h.add_argument("--hbm-channels", type=int, default=1)
    h.add_argument("--frontend-clock-ratio", type=int, default=4)
    h.add_argument("--memory-clock-ratio", type=int, default=1)
    h.add_argument("--scheduler", default="FRFCFSRowHit")
    h.add_argument("--row-policy", default="Open")
    h.add_argument("--addr-mapper", default="RoBaRaCoCh")
    h.add_argument("--refresh-manager", default="HBM34PerBankRefresh")
    h.add_argument("--transaction-bytes", type=int, default=32)
    h.set_defaults(func=cmd_hbm_trace)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
