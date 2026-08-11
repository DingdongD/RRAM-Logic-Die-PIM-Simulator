from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class TrafficCounters:
    accesses: int = 0
    useful_bits: int = 0
    transferred_bits: int = 0
    dynamic_energy_pj: float = 0.0
    queue_stall_cycles: int = 0
    max_queue_occupancy: int = 0

    def add(self, other: "TrafficCounters") -> None:
        self.accesses += other.accesses
        self.useful_bits += other.useful_bits
        self.transferred_bits += other.transferred_bits
        self.dynamic_energy_pj += other.dynamic_energy_pj
        self.queue_stall_cycles += other.queue_stall_cycles
        self.max_queue_occupancy = max(self.max_queue_occupancy, other.max_queue_occupancy)


@dataclass
class FetchResult:
    start_cycle: int
    ready_cycle: int
    source: TrafficCounters
    stages: Dict[str, TrafficCounters]

    @property
    def cycles(self) -> int:
        return self.ready_cycle - self.start_cycle


@dataclass
class SimulationResult:
    architecture: str
    cycles: int
    time_ns: float
    macs: int
    pe_utilization: float
    stall_cycles_weight: int
    weight_useful_bits: int
    weight_source_transferred_bits: int
    energy_pj: Dict[str, float] = field(default_factory=dict)
    traffic_bits: Dict[str, int] = field(default_factory=dict)
    queue_stall_cycles: Dict[str, int] = field(default_factory=dict)
    max_queue_occupancy: Dict[str, int] = field(default_factory=dict)

    @property
    def total_energy_pj(self) -> float:
        return sum(self.energy_pj.values())

    def to_dict(self) -> Dict[str, object]:
        return {
            "architecture": self.architecture,
            "cycles": self.cycles,
            "time_ns": self.time_ns,
            "macs": self.macs,
            "pe_utilization": self.pe_utilization,
            "stall_cycles_weight": self.stall_cycles_weight,
            "weight_useful_bits": self.weight_useful_bits,
            "weight_source_transferred_bits": self.weight_source_transferred_bits,
            "weight_overfetch_ratio": (
                self.weight_source_transferred_bits / self.weight_useful_bits
                if self.weight_useful_bits else 0.0
            ),
            "energy_pj": dict(self.energy_pj),
            "total_energy_pj": self.total_energy_pj,
            "traffic_bits": dict(self.traffic_bits),
            "queue_stall_cycles": dict(self.queue_stall_cycles),
            "max_queue_occupancy": dict(self.max_queue_occupancy),
        }
