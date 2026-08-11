from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


def _positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


def _non_negative(name: str, value: float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")


@dataclass(frozen=True)
class ClockConfig:
    freq_hz: float = 1e9

    def __post_init__(self) -> None:
        _positive("freq_hz", self.freq_hz)

    @property
    def period_ns(self) -> float:
        return 1e9 / self.freq_hz


@dataclass(frozen=True)
class BankedMemoryConfig:
    """Timing/energy contract for a banked backing memory.

    `read_granule_bits` is both the physical transfer granularity and the
    address-striping granularity. `issue_interval_cycles` is the minimum
    distance between reads issued to the same bank. Reads to different banks
    may overlap. `read_latency_cycles` is issue-to-data-ready latency.
    """

    name: str
    num_banks: int
    capacity_bytes: int
    read_granule_bits: int
    read_latency_cycles: int
    issue_interval_cycles: int
    read_energy_pj_per_access: float
    static_power_mw: float = 0.0

    def __post_init__(self) -> None:
        _positive("num_banks", self.num_banks)
        _positive("capacity_bytes", self.capacity_bytes)
        _positive("read_granule_bits", self.read_granule_bits)
        _positive("read_latency_cycles", self.read_latency_cycles)
        _positive("issue_interval_cycles", self.issue_interval_cycles)
        _non_negative("read_energy_pj_per_access", self.read_energy_pj_per_access)
        _non_negative("static_power_mw", self.static_power_mw)

    @property
    def peak_bits_per_cycle(self) -> float:
        return self.num_banks * self.read_granule_bits / self.issue_interval_cycles


@dataclass(frozen=True)
class StreamStageConfig:
    """A pipelined transfer stage (TSV, NoC, SRAM port, etc.).

    For TSVs, `latency_cycles` and `energy_pj_per_bit` should be calibrated
    from a physical TSV model such as DESTINY/CACTI-3DD. The aggregate
    `width_bits_per_cycle` is an architectural number of simultaneously driven
    data TSV lanes; it is not inferred from the physical TSV RC model.
    """

    name: str
    width_bits_per_cycle: int
    latency_cycles: int
    energy_pj_per_bit: float
    efficiency: float = 1.0
    static_power_mw: float = 0.0
    area_um2_per_lane: float = 0.0
    provenance: str = ""

    def __post_init__(self) -> None:
        _positive("width_bits_per_cycle", self.width_bits_per_cycle)
        _non_negative("latency_cycles", self.latency_cycles)
        _non_negative("energy_pj_per_bit", self.energy_pj_per_bit)
        if not 0 < self.efficiency <= 1.0:
            raise ValueError("efficiency must be in (0, 1]")
        _non_negative("static_power_mw", self.static_power_mw)
        _non_negative("area_um2_per_lane", self.area_um2_per_lane)

    @property
    def effective_bits_per_cycle(self) -> float:
        return self.width_bits_per_cycle * self.efficiency

    @property
    def aggregate_area_um2(self) -> float:
        return self.width_bits_per_cycle * self.area_um2_per_lane


@dataclass(frozen=True)
class FIFOConfig:
    """Finite architectural FIFO/credit contract.

    This is intentionally separate from the physical TSV model. A FIFO entry
    stores one packet of at most `packet_bits`; `depth_entries` is the number
    of outstanding receive slots. If `backpressure_to_source` is true, the
    transfer path prevents a producer from issuing a response that cannot be
    accepted by the finite downstream queue.
    """

    name: str = "tsv_rx_fifo"
    depth_entries: int = 8
    packet_bits: int = 256
    backpressure_to_source: bool = True
    static_power_mw: float = 0.0

    def __post_init__(self) -> None:
        _positive("depth_entries", self.depth_entries)
        _positive("packet_bits", self.packet_bits)
        _non_negative("static_power_mw", self.static_power_mw)


@dataclass(frozen=True)
class WBufferBankingConfig:
    """Bank/port organization for each ping-pong WBUF half.

    V0.2 models the two ping-pong halves as independent bank sets. This makes
    fill-vs-consume overlap explicit without inventing undocumented shared-port
    arbitration. A future shared-bank mode must add an arbitration policy
    rather than silently reusing this model.
    """

    num_banks: int = 16
    bank_word_bits: int = 64
    read_ports_per_bank: int = 1
    write_ports_per_bank: int = 1
    read_latency_cycles: int = 1
    write_latency_cycles: int = 1
    independent_ping_pong_banks: bool = True

    def __post_init__(self) -> None:
        _positive("num_banks", self.num_banks)
        _positive("bank_word_bits", self.bank_word_bits)
        _positive("read_ports_per_bank", self.read_ports_per_bank)
        _positive("write_ports_per_bank", self.write_ports_per_bank)
        _non_negative("read_latency_cycles", self.read_latency_cycles)
        _non_negative("write_latency_cycles", self.write_latency_cycles)
        if not self.independent_ping_pong_banks:
            raise NotImplementedError(
                "Shared-bank ping-pong arbitration is not implemented; "
                "use independent_ping_pong_banks=True"
            )


@dataclass(frozen=True)
class WBufferConfig:
    capacity_bytes: int = 32 * 1024
    read_bits_per_cycle: int = 1024
    write_stage: StreamStageConfig = field(
        default_factory=lambda: StreamStageConfig(
            name="wbuf_write",
            width_bits_per_cycle=1024,
            latency_cycles=1,
            energy_pj_per_bit=0.0,
        )
    )
    banking: WBufferBankingConfig = field(default_factory=WBufferBankingConfig)
    read_energy_pj_per_bit: float = 0.0
    static_power_mw: float = 0.0

    def __post_init__(self) -> None:
        _positive("capacity_bytes", self.capacity_bytes)
        _positive("read_bits_per_cycle", self.read_bits_per_cycle)
        _non_negative("read_energy_pj_per_bit", self.read_energy_pj_per_bit)
        _non_negative("static_power_mw", self.static_power_mw)

    @property
    def half_capacity_bits(self) -> int:
        return self.capacity_bytes * 8 // 2


@dataclass(frozen=True)
class PELineConfig:
    num_pe: int = 128
    psum_contexts: int = 1
    mac_energy_pj: float = 0.0
    accumulator_energy_pj: float = 0.0
    startup_cycles_per_tile: int = 0
    static_power_mw: float = 0.0

    def __post_init__(self) -> None:
        _positive("num_pe", self.num_pe)
        _positive("psum_contexts", self.psum_contexts)
        _non_negative("mac_energy_pj", self.mac_energy_pj)
        _non_negative("accumulator_energy_pj", self.accumulator_energy_pj)
        _non_negative("startup_cycles_per_tile", self.startup_cycles_per_tile)
        _non_negative("static_power_mw", self.static_power_mw)


@dataclass(frozen=True)
class SystemConfig:
    clock: ClockConfig
    pe: PELineConfig
    wbuf: WBufferConfig
    rram: BankedMemoryConfig
    tsv: StreamStageConfig
    hbm: BankedMemoryConfig
    npu_direct_stages: List[StreamStageConfig]
    npu_hierarchical_stages: List[StreamStageConfig]
    tsv_fifo: FIFOConfig = field(default_factory=FIFOConfig)
    metadata: Dict[str, str] = field(default_factory=dict)
