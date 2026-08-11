from __future__ import annotations

from enum import Enum
from typing import Dict

from .components import (
    BankedReadMemory,
    BankedWBufferWriteStage,
    PELine,
    RRAMTSVWBufferPath,
    StreamStage,
    TransferPath,
)
from .config import SystemConfig
from .mapping import GemmMapper
from .stats import SimulationResult, TrafficCounters
from .workload import GemmWorkload


class ArchitectureKind(str, Enum):
    RRAM_NMP = "rram_nmp"
    NPU_DIRECT = "npu_direct"
    NPU_HIERARCHICAL = "npu_hierarchical"


class CycleEventSimulator:
    """Transaction/event CModel with explicit ping-pong WBUF overlap.

    RRAM-NMP couples banked RRAM timing, calibrated physical TSV transfer,
    finite TSV receive FIFO credits/backpressure, and bank/port-aware WBUF
    filling. Pure-NPU baselines share the same banked WBUF organization.
    """

    def __init__(self, cfg: SystemConfig) -> None:
        self.cfg = cfg

    def _make_path(self, arch: ArchitectureKind):
        if arch == ArchitectureKind.RRAM_NMP:
            return RRAMTSVWBufferPath(
                source=BankedReadMemory(self.cfg.rram),
                tsv_cfg=self.cfg.tsv,
                fifo_cfg=self.cfg.tsv_fifo,
                wbuf_cfg=self.cfg.wbuf,
                name=arch.value,
            )
        if arch == ArchitectureKind.NPU_DIRECT:
            source_cfg = self.cfg.hbm
            stage_cfgs = self.cfg.npu_direct_stages
        elif arch == ArchitectureKind.NPU_HIERARCHICAL:
            source_cfg = self.cfg.hbm
            stage_cfgs = self.cfg.npu_hierarchical_stages
        else:
            raise ValueError(f"Unsupported architecture {arch}")

        return TransferPath(
            source=BankedReadMemory(source_cfg),
            stages=[
                *[StreamStage(x) for x in stage_cfgs],
                BankedWBufferWriteStage(self.cfg.wbuf),
            ],
            name=arch.value,
        )

    def _static_power_mw(self, arch: ArchitectureKind, path) -> float:
        static_mw = self.cfg.pe.static_power_mw + self.cfg.wbuf.static_power_mw
        static_mw += path.source.cfg.static_power_mw
        if arch == ArchitectureKind.RRAM_NMP:
            static_mw += self.cfg.tsv.static_power_mw
            static_mw += self.cfg.tsv_fifo.static_power_mw
        else:
            for stage in path.stages:
                # WBUF macro static power is accounted once via WBufferConfig.
                if isinstance(stage, BankedWBufferWriteStage):
                    continue
                static_mw += stage.cfg.static_power_mw
        return static_mw

    def run(self, workload: GemmWorkload, arch: ArchitectureKind) -> SimulationResult:
        mapper = GemmMapper(workload, self.cfg.pe, self.cfg.wbuf)
        tiles = mapper.tiles()
        if not tiles:
            raise AssertionError("Non-empty GEMM produced no tiles")

        path = self._make_path(arch)
        pe = PELine(self.cfg.pe, self.cfg.wbuf, workload.weight_bits)

        source_total = TrafficCounters()
        stage_totals: Dict[str, TrafficCounters] = {}
        compute_dyn_pj = 0.0
        stall_weight = 0

        def accumulate_fetch(fetch) -> None:
            source_total.add(fetch.source)
            for name, stats in fetch.stages.items():
                stage_totals.setdefault(name, TrafficCounters()).add(stats)

        # Tile 0 fills WBUF-A. While it computes, tile 1 fills WBUF-B. The
        # mapper constrains each tile to one WBUF half, so a half is not reused
        # for a new fill until its previous compute has completed.
        first = path.fetch(tiles[0].weight_segments(workload), start_cycle=0)
        accumulate_fetch(first)
        total_cycle = first.ready_cycle

        next_fetch = None
        if len(tiles) > 1:
            next_fetch = path.fetch(
                tiles[1].weight_segments(workload),
                start_cycle=total_cycle,
            )
            accumulate_fetch(next_fetch)

        for i, tile in enumerate(tiles):
            compute_cycles = pe.compute_cycles(tile)
            compute_dyn_pj += pe.dynamic_energy_pj(tile)
            compute_end = total_cycle + compute_cycles

            if i == len(tiles) - 1:
                total_cycle = compute_end
                break

            assert next_fetch is not None
            next_ready = next_fetch.ready_cycle
            if next_ready > compute_end:
                stall_weight += next_ready - compute_end
            total_cycle = max(compute_end, next_ready)

            if i + 2 < len(tiles):
                next_fetch = path.fetch(
                    tiles[i + 2].weight_segments(workload),
                    start_cycle=total_cycle,
                )
                accumulate_fetch(next_fetch)
            else:
                next_fetch = None

        macs = workload.macs
        theoretical_pe_slots = self.cfg.pe.num_pe * total_cycle
        utilization = macs / theoretical_pe_slots if theoretical_pe_slots else 0.0
        period_ns = self.cfg.clock.period_ns
        time_ns = total_cycle * period_ns

        energy: Dict[str, float] = {
            "compute_and_wbuf_read_dynamic": compute_dyn_pj,
            f"{path.source.cfg.name}_dynamic": source_total.dynamic_energy_pj,
        }
        traffic: Dict[str, int] = {
            f"{path.source.cfg.name}_useful": source_total.useful_bits,
            f"{path.source.cfg.name}_transferred": source_total.transferred_bits,
        }
        queue_stalls: Dict[str, int] = {}
        max_occupancy: Dict[str, int] = {}
        for name, stats in stage_totals.items():
            energy[f"{name}_dynamic"] = stats.dynamic_energy_pj
            traffic[f"{name}_transferred"] = stats.transferred_bits
            if stats.queue_stall_cycles:
                queue_stalls[name] = stats.queue_stall_cycles
            if stats.max_queue_occupancy:
                max_occupancy[name] = stats.max_queue_occupancy

        # mW * ns = pJ.
        energy["static"] = self._static_power_mw(arch, path) * time_ns

        return SimulationResult(
            architecture=arch.value,
            cycles=total_cycle,
            time_ns=time_ns,
            macs=macs,
            pe_utilization=utilization,
            stall_cycles_weight=stall_weight,
            weight_useful_bits=source_total.useful_bits,
            weight_source_transferred_bits=source_total.transferred_bits,
            energy_pj=energy,
            traffic_bits=traffic,
            queue_stall_cycles=queue_stalls,
            max_queue_occupancy=max_occupancy,
        )

    def compare(self, workload: GemmWorkload) -> Dict[str, object]:
        nmp = self.run(workload, ArchitectureKind.RRAM_NMP)
        direct = self.run(workload, ArchitectureKind.NPU_DIRECT)
        hierarchical = self.run(workload, ArchitectureKind.NPU_HIERARCHICAL)
        return {
            "rram_nmp": nmp.to_dict(),
            "npu_direct": direct.to_dict(),
            "npu_hierarchical": hierarchical.to_dict(),
            "speedup_vs_direct": direct.cycles / nmp.cycles,
            "speedup_vs_hierarchical": hierarchical.cycles / nmp.cycles,
            "energy_ratio_vs_direct": (
                direct.total_energy_pj / nmp.total_energy_pj
                if nmp.total_energy_pj else None
            ),
            "energy_ratio_vs_hierarchical": (
                hierarchical.total_energy_pj / nmp.total_energy_pj
                if nmp.total_energy_pj else None
            ),
        }
