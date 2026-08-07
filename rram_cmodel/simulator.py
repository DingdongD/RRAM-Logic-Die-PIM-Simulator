from __future__ import annotations

from enum import Enum
from typing import Dict

from .components import BankedReadMemory, PELine, StreamStage, TransferPath
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

    Every physical source read granule is mapped to a bank, bank issue
    constraints are enforced, packets traverse serialized transfer stages, and
    the PE line starts only when the corresponding WBUF half is ready.
    """

    def __init__(self, cfg: SystemConfig) -> None:
        self.cfg = cfg

    def _make_path(self, arch: ArchitectureKind) -> TransferPath:
        if arch == ArchitectureKind.RRAM_NMP:
            source_cfg = self.cfg.rram
            stage_cfgs = [self.cfg.tsv, self.cfg.wbuf.write_stage]
        elif arch == ArchitectureKind.NPU_DIRECT:
            source_cfg = self.cfg.hbm
            stage_cfgs = [*self.cfg.npu_direct_stages, self.cfg.wbuf.write_stage]
        elif arch == ArchitectureKind.NPU_HIERARCHICAL:
            source_cfg = self.cfg.hbm
            stage_cfgs = [*self.cfg.npu_hierarchical_stages, self.cfg.wbuf.write_stage]
        else:
            raise ValueError(f"Unsupported architecture {arch}")
        return TransferPath(
            source=BankedReadMemory(source_cfg),
            stages=[StreamStage(x) for x in stage_cfgs],
            name=arch.value,
        )

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

        # Prioritize tile 0 into WBUF-A. When compute starts, launch tile 1
        # into WBUF-B. On every swap the just-consumed half is reused for i+2.
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
        for name, stats in stage_totals.items():
            energy[f"{name}_dynamic"] = stats.dynamic_energy_pj
            traffic[f"{name}_transferred"] = stats.transferred_bits

        # mW * ns = pJ.
        static_mw = self.cfg.pe.static_power_mw + self.cfg.wbuf.static_power_mw
        static_mw += path.source.cfg.static_power_mw
        for stage in path.stages:
            static_mw += stage.cfg.static_power_mw
        energy["static"] = static_mw * time_ns

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
