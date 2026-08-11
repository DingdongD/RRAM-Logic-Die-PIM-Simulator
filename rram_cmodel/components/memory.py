from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil
from typing import Dict, List, Sequence, Tuple

from ..config import BankedMemoryConfig, FIFOConfig, StreamStageConfig, WBufferConfig
from ..mapping import BitSegment
from ..stats import FetchResult, TrafficCounters


@dataclass(frozen=True)
class Packet:
    index: int
    ready_cycle: int
    bits: int
    address_line: int = -1
    bank: int = -1
    issue_cycle: int = -1


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

    def granule_lines(self, segments: Sequence[BitSegment]) -> List[int]:
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

    # Kept for compatibility with V0.1 tests/private callers.
    def _granule_lines(self, segments: Sequence[BitSegment]) -> List[int]:
        return self.granule_lines(segments)

    def issue_line(self, line: int, index: int, start_cycle: int) -> Packet:
        bank = line % self.cfg.num_banks
        issue = max(start_cycle, self._next_issue[bank])
        self._next_issue[bank] = issue + self.cfg.issue_interval_cycles
        return Packet(
            index=index,
            ready_cycle=issue + self.cfg.read_latency_cycles,
            bits=self.cfg.read_granule_bits,
            address_line=line,
            bank=bank,
            issue_cycle=issue,
        )

    def block_bank_until(self, bank: int, cycle: int) -> None:
        """Propagate downstream credit backpressure to a source bank.

        This models a source interface without an unbounded hidden response
        buffer: if the TSV path cannot accept a completed response, later
        responses from the same bank cannot be launched ahead of the credit.
        """

        if bank < 0 or bank >= self.cfg.num_banks:
            raise ValueError(f"Invalid bank {bank}")
        self._next_issue[bank] = max(self._next_issue[bank], cycle)

    def schedule_read(
        self,
        segments: Sequence[BitSegment],
        start_cycle: int,
    ) -> Tuple[List[Packet], TrafficCounters]:
        lines = self.granule_lines(segments)
        useful_bits = sum(s.size_bits for s in segments)
        packets = [self.issue_line(line, idx, start_cycle) for idx, line in enumerate(lines)]
        counters = TrafficCounters(
            accesses=len(lines),
            useful_bits=useful_bits,
            transferred_bits=len(lines) * self.cfg.read_granule_bits,
            dynamic_energy_pj=len(lines) * self.cfg.read_energy_pj_per_access,
        )
        return packets, counters


class StreamStage:
    """A single-server pipelined transfer stage with cycle-level bandwidth."""

    def __init__(self, cfg: StreamStageConfig) -> None:
        self.cfg = cfg
        self._next_free_cycle = 0

    def reset(self) -> None:
        self._next_free_cycle = 0

    def schedule_packet(self, p: Packet) -> Tuple[Packet, TrafficCounters]:
        start = max(p.ready_cycle, self._next_free_cycle)
        ser = max(1, ceil(p.bits / self.cfg.effective_bits_per_cycle))
        self._next_free_cycle = start + ser
        out = replace(p, ready_cycle=self._next_free_cycle + self.cfg.latency_cycles)
        return out, TrafficCounters(
            accesses=1,
            useful_bits=p.bits,
            transferred_bits=p.bits,
            dynamic_energy_pj=p.bits * self.cfg.energy_pj_per_bit,
        )

    def schedule(self, packets: Sequence[Packet]) -> Tuple[List[Packet], TrafficCounters]:
        ordered = sorted(packets, key=lambda p: (p.ready_cycle, p.index))
        out: List[Packet] = []
        total = TrafficCounters()
        for p in ordered:
            q, stats = self.schedule_packet(p)
            out.append(q)
            total.add(stats)
        out.sort(key=lambda p: p.index)
        return out, total


class BankedWBufferWriteStage:
    """Bank/port-aware WBUF fill stage.

    Source packets are repacketized into WBUF bank words. Consecutive bank
    words are striped across banks; every bank owns an explicit set of write
    ports with independent next-issue cycles. A packet becomes visible only
    after all physical words belonging to it have completed their writes.
    """

    def __init__(self, cfg: WBufferConfig) -> None:
        self.wbuf_cfg = cfg
        self.cfg = cfg.write_stage
        banking = cfg.banking
        self._ports = [
            [0 for _ in range(banking.write_ports_per_bank)]
            for _ in range(banking.num_banks)
        ]

    def reset(self) -> None:
        banking = self.wbuf_cfg.banking
        self._ports = [
            [0 for _ in range(banking.write_ports_per_bank)]
            for _ in range(banking.num_banks)
        ]

    def _schedule_word(self, word_index: int, ready_cycle: int) -> Tuple[int, int]:
        bcfg = self.wbuf_cfg.banking
        bank = word_index % bcfg.num_banks
        port_times = self._ports[bank]
        port = min(range(len(port_times)), key=lambda i: port_times[i])
        start = max(ready_cycle, port_times[port])
        # One bank word can be accepted per write port per cycle. Write latency
        # affects visibility, not the next issue interval.
        port_times[port] = start + 1
        return start, start + bcfg.write_latency_cycles

    def schedule_packet(self, p: Packet) -> Tuple[Packet, TrafficCounters]:
        bcfg = self.wbuf_cfg.banking
        nwords = max(1, ceil(p.bits / bcfg.bank_word_bits))
        starts: List[int] = []
        completes: List[int] = []
        base_word = p.index * nwords
        for off in range(nwords):
            start, done = self._schedule_word(base_word + off, p.ready_cycle)
            starts.append(start)
            completes.append(done)
        physical_bits = nwords * bcfg.bank_word_bits
        out = replace(p, ready_cycle=max(completes))
        return out, TrafficCounters(
            accesses=nwords,
            useful_bits=p.bits,
            transferred_bits=physical_bits,
            dynamic_energy_pj=physical_bits * self.cfg.energy_pj_per_bit,
        )

    def schedule(self, packets: Sequence[Packet]) -> Tuple[List[Packet], TrafficCounters]:
        out: List[Packet] = []
        total = TrafficCounters()
        for p in sorted(packets, key=lambda x: (x.ready_cycle, x.index)):
            q, stats = self.schedule_packet(p)
            out.append(q)
            total.add(stats)
        out.sort(key=lambda p: p.index)
        return out, total


class TransferPath:
    """Backing memory followed by streaming stages ending at WBUF write."""

    def __init__(self, source: BankedReadMemory, stages: Sequence[object], name: str) -> None:
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
        ready = start_cycle if not packets else max(p.ready_cycle for p in packets)
        return FetchResult(
            start_cycle=start_cycle,
            ready_cycle=ready,
            source=source_stats,
            stages=stage_stats,
        )


class RRAMTSVWBufferPath:
    """Coupled RRAM -> physical TSV -> finite FIFO -> banked WBUF path.

    Physical TSV delay/energy come from `StreamStageConfig` and should be
    calibrated from DESTINY/CACTI-3DD. FIFO depth/credits and WBUF banking are
    architectural parameters. The finite FIFO is placed at the TSV receive
    side. A FIFO entry is released only when the whole packet is accepted by
    the WBUF write banks. If the FIFO is full, TSV completion is delayed; with
    `backpressure_to_source=True`, that delay is propagated to the source bank.
    """

    def __init__(
        self,
        source: BankedReadMemory,
        tsv_cfg: StreamStageConfig,
        fifo_cfg: FIFOConfig,
        wbuf_cfg: WBufferConfig,
        name: str = "rram_nmp",
    ) -> None:
        self.source = source
        self.tsv_cfg = tsv_cfg
        self.fifo_cfg = fifo_cfg
        self.wbuf = BankedWBufferWriteStage(wbuf_cfg)
        self.name = name
        self._tsv_next_free = 0
        self._fifo_release_cycles: List[int] = []
        self._max_fifo_occupancy = 0

    @property
    def stages(self) -> Sequence[object]:
        # Used by static-energy accounting in the system simulator.
        return []

    def reset(self) -> None:
        self.source.reset()
        self.wbuf.reset()
        self._tsv_next_free = 0
        self._fifo_release_cycles = []
        self._max_fifo_occupancy = 0

    def _fifo_admission(self, arrival: int) -> int:
        releases = sorted(x for x in self._fifo_release_cycles if x > arrival)
        self._fifo_release_cycles = releases
        occupancy = len(releases)
        self._max_fifo_occupancy = max(self._max_fifo_occupancy, occupancy)
        if occupancy < self.fifo_cfg.depth_entries:
            return arrival
        # Earliest queued packet starts its WBUF write and releases one credit.
        return releases[0]

    def _schedule_tsv_and_wbuf(self, p: Packet) -> Tuple[Packet, TrafficCounters, TrafficCounters, int]:
        ser = max(1, ceil(p.bits / self.tsv_cfg.effective_bits_per_cycle))
        tsv_start = max(p.ready_cycle, self._tsv_next_free)
        raw_arrival = tsv_start + ser + self.tsv_cfg.latency_cycles
        admit = self._fifo_admission(raw_arrival)
        queue_stall = max(0, admit - raw_arrival)

        if queue_stall:
            # The receive FIFO has no credit. Hold the TSV serializer so that
            # the packet reaches the receive side exactly when a credit frees.
            tsv_start += queue_stall
            raw_arrival += queue_stall
            admit = raw_arrival

        self._tsv_next_free = tsv_start + ser
        tsv_packet = replace(p, ready_cycle=admit)
        wbuf_packet, wbuf_stats = self.wbuf.schedule_packet(tsv_packet)

        # Conservative packet credit: the FIFO entry is held until all bank
        # words of the packet have been accepted/committed by the WBUF stage.
        release = wbuf_packet.ready_cycle
        self._fifo_release_cycles.append(release)
        active_now = sum(1 for x in self._fifo_release_cycles if x > admit)
        self._max_fifo_occupancy = max(self._max_fifo_occupancy, active_now)

        tsv_stats = TrafficCounters(
            accesses=1,
            useful_bits=p.bits,
            transferred_bits=p.bits,
            dynamic_energy_pj=p.bits * self.tsv_cfg.energy_pj_per_bit,
            queue_stall_cycles=queue_stall,
            max_queue_occupancy=self._max_fifo_occupancy,
        )
        return wbuf_packet, tsv_stats, wbuf_stats, tsv_start

    def fetch(self, segments: Sequence[BitSegment], start_cycle: int) -> FetchResult:
        lines = self.source.granule_lines(segments)
        useful_bits = sum(seg.size_bits for seg in segments)
        source_stats = TrafficCounters(
            accesses=len(lines),
            useful_bits=useful_bits,
            transferred_bits=len(lines) * self.source.cfg.read_granule_bits,
            dynamic_energy_pj=len(lines) * self.source.cfg.read_energy_pj_per_access,
        )
        tsv_total = TrafficCounters()
        wbuf_total = TrafficCounters()
        packets: List[Packet] = []

        for idx, line in enumerate(lines):
            p = self.source.issue_line(line, idx, start_cycle)
            q, tsv_stats, wbuf_stats, tsv_start = self._schedule_tsv_and_wbuf(p)
            if self.fifo_cfg.backpressure_to_source and p.bank >= 0:
                self.source.block_bank_until(p.bank, tsv_start)
            tsv_total.add(tsv_stats)
            wbuf_total.add(wbuf_stats)
            packets.append(q)

        ready = start_cycle if not packets else max(p.ready_cycle for p in packets)
        return FetchResult(
            start_cycle=start_cycle,
            ready_cycle=ready,
            source=source_stats,
            stages={self.tsv_cfg.name: tsv_total, self.wbuf.cfg.name: wbuf_total},
        )
