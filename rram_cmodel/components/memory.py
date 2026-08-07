from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Dict, List, Sequence, Tuple

from ..config import BankedMemoryConfig, StreamStageConfig
from ..mapping import BitSegment
from ..stats import FetchResult, TrafficCounters


@dataclass(frozen=True)
class Packet:
    index: int
    ready_cycle: int
    bits: int


class BankedReadMemory:
    """Cycle-accurate-at-request-granularity banked read source.

    The model tracks the next legal issue cycle independently for every bank.
    Granule reads can overlap across banks, and pipelined reads can overlap
    within a bank according to `issue_interval_cycles`.
    """

    def __init__(self, cfg: BankedMemoryConfig) -> None:
        self.cfg = cfg
        self._next_issue = [0 for _ in range(cfg.num_banks)]

    def reset(self) -> None:
        self._next_issue = [0 for _ in range(self.cfg.num_banks)]

    def _granule_lines(self, segments: Sequence[BitSegment]) -> List[int]:
        gran = self.cfg.read_granule_bits
        unique = set()
        capacity_bits = self.cfg.capacity_bytes * 8
        for seg in segments:
            if seg.address_bit < 0 or seg.size_bits <= 0:
                raise ValueError("Invalid memory segment")
            end = seg.address_bit + seg.size_bits
            if end > capacity_bits:
                raise ValueError(
                    f"Read exceeds {self.cfg.name} capacity: end_bit={end}, "
                    f"capacity_bits={capacity_bits}"
                )
            first = seg.address_bit // gran
            last = (end - 1) // gran
            for line in range(first, last + 1):
                unique.add(line)
        return sorted(unique)

    def schedule_read(
        self,
        segments: Sequence[BitSegment],
        start_cycle: int,
    ) -> Tuple[List[Packet], TrafficCounters]:
        lines = self._granule_lines(segments)
        useful_bits = sum(s.size_bits for s in segments)
        packets: List[Packet] = []
        gran = self.cfg.read_granule_bits
        for idx, line in enumerate(lines):
            bank = line % self.cfg.num_banks
            issue = max(start_cycle, self._next_issue[bank])
            self._next_issue[bank] = issue + self.cfg.issue_interval_cycles
            ready = issue + self.cfg.read_latency_cycles
            packets.append(Packet(index=idx, ready_cycle=ready, bits=gran))

        counters = TrafficCounters(
            accesses=len(lines),
            useful_bits=useful_bits,
            transferred_bits=len(lines) * gran,
            dynamic_energy_pj=len(lines) * self.cfg.read_energy_pj_per_access,
        )
        return packets, counters


class StreamStage:
    """A single-server pipelined transfer stage with cycle-level bandwidth.

    Packets are arbitrated by readiness and original packet index. Once a
    packet starts, serialization occupies the stage for ceil(bits/BW) cycles;
    configured latency is then added before downstream visibility.
    """

    def __init__(self, cfg: StreamStageConfig) -> None:
        self.cfg = cfg
        self._next_free_cycle = 0

    def reset(self) -> None:
        self._next_free_cycle = 0

    def schedule(self, packets: Sequence[Packet]) -> Tuple[List[Packet], TrafficCounters]:
        ordered = sorted(packets, key=lambda p: (p.ready_cycle, p.index))
        out: List[Packet] = []
        transferred = 0
        for p in ordered:
            start = max(p.ready_cycle, self._next_free_cycle)
            ser = max(1, ceil(p.bits / self.cfg.effective_bits_per_cycle))
            self._next_free_cycle = start + ser
            ready = self._next_free_cycle + self.cfg.latency_cycles
            out.append(Packet(index=p.index, ready_cycle=ready, bits=p.bits))
            transferred += p.bits
        out.sort(key=lambda p: p.index)
        return out, TrafficCounters(
            accesses=len(packets),
            useful_bits=transferred,
            transferred_bits=transferred,
            dynamic_energy_pj=transferred * self.cfg.energy_pj_per_bit,
        )


class TransferPath:
    """Backing memory followed by streaming stages ending at WBUF write."""

    def __init__(
        self,
        source: BankedReadMemory,
        stages: Sequence[StreamStage],
        name: str,
    ) -> None:
        self.source = source
        self.stages = list(stages)
        self.name = name

    def reset(self) -> None:
        self.source.reset()
        for stage in self.stages:
            stage.reset()

    def fetch(self, segments: Sequence[BitSegment], start_cycle: int) -> FetchResult:
        packets, source_stats = self.source.schedule_read(segments, start_cycle)
        stage_stats: Dict[str, TrafficCounters] = {}
        for stage in self.stages:
            packets, stats = stage.schedule(packets)
            stage_stats[stage.cfg.name] = stats
        ready = start_cycle
        if packets:
            ready = max(p.ready_cycle for p in packets)
        return FetchResult(
            start_cycle=start_cycle,
            ready_cycle=ready,
            source=source_stats,
            stages=stage_stats,
        )
