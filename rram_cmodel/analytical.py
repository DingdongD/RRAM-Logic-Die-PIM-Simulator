from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Sequence

from .components.compute import PELine
from .config import BankedMemoryConfig, SystemConfig
from .mapping import BitSegment, GemmMapper
from .simulator import ArchitectureKind
from .workload import GemmWorkload


@dataclass(frozen=True)
class AnalyticalResult:
    architecture: str
    cycles: int
    stall_cycles_weight: int
    pe_utilization: float


class FastAnalyticalSimulator:
    """Closed-form cross-check model, never the publication timing backend.

    It uses the same mapping and physical-granule accounting as the event model,
    but collapses each source/path into aggregate bandwidth plus fixed latency.
    """

    def __init__(self, cfg: SystemConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _source_transfer_bits(segments: Sequence[BitSegment], source: BankedMemoryConfig) -> int:
        gran = source.read_granule_bits
        lines = set()
        for seg in segments:
            first = seg.address_bit // gran
            last = (seg.address_bit + seg.size_bits - 1) // gran
            lines.update(range(first, last + 1))
        return len(lines) * gran

    def _path(self, arch: ArchitectureKind):
        if arch == ArchitectureKind.RRAM_NMP:
            return self.cfg.rram, [self.cfg.tsv, self.cfg.wbuf.write_stage]
        if arch == ArchitectureKind.NPU_DIRECT:
            return self.cfg.hbm, [*self.cfg.npu_direct_stages, self.cfg.wbuf.write_stage]
        if arch == ArchitectureKind.NPU_HIERARCHICAL:
            return self.cfg.hbm, [*self.cfg.npu_hierarchical_stages, self.cfg.wbuf.write_stage]
        raise ValueError(arch)

    def _fetch_cycles(self, segments: Sequence[BitSegment], arch: ArchitectureKind) -> int:
        source, stages = self._path(arch)
        bits = self._source_transfer_bits(segments, source)
        bw = min([source.peak_bits_per_cycle] + [x.effective_bits_per_cycle for x in stages])
        fixed_latency = source.read_latency_cycles + sum(x.latency_cycles for x in stages)
        return fixed_latency + ceil(bits / bw)

    def run(self, workload: GemmWorkload, arch: ArchitectureKind) -> AnalyticalResult:
        tiles = GemmMapper(workload, self.cfg.pe, self.cfg.wbuf).tiles()
        pe = PELine(self.cfg.pe, self.cfg.wbuf, workload.weight_bits)
        fetch = [self._fetch_cycles(t.weight_segments(workload), arch) for t in tiles]
        comp = [pe.compute_cycles(t) for t in tiles]

        cycle = fetch[0]
        stall = 0
        for i in range(len(tiles) - 1):
            c = comp[i]
            f = fetch[i + 1]
            stall += max(0, f - c)
            cycle += max(c, f)
        cycle += comp[-1]
        util = workload.macs / (self.cfg.pe.num_pe * cycle)
        return AnalyticalResult(
            architecture=arch.value,
            cycles=cycle,
            stall_cycles_weight=stall,
            pe_utilization=util,
        )
