from __future__ import annotations

from math import ceil
from typing import List

from ..config import PELineConfig, WBufferConfig
from ..mapping import WeightTile


class PELine:
    """Output-stationary PE-line timing and energy model.

    WBUF supply is constrained by both the aggregate read datapath and the
    explicit bank/port organization. Consecutive bank words of one output-
    channel vector are striped across banks. Bank read latency is modeled as
    pipeline startup; per-vector throughput is determined by the slowest bank.
    """

    def __init__(self, pe_cfg: PELineConfig, wbuf_cfg: WBufferConfig, weight_bits: int) -> None:
        self.pe_cfg = pe_cfg
        self.wbuf_cfg = wbuf_cfg
        self.weight_bits = weight_bits
        if weight_bits <= 0:
            raise ValueError("weight_bits must be > 0")
        if wbuf_cfg.read_bits_per_cycle < weight_bits:
            raise ValueError("WBUF read bandwidth is smaller than one weight")

    def _bank_word_counts(self, nt: int) -> List[int]:
        bcfg = self.wbuf_cfg.banking
        vector_bits = nt * self.weight_bits
        nwords = max(1, ceil(vector_bits / bcfg.bank_word_bits))
        counts = [0 for _ in range(bcfg.num_banks)]
        for word in range(nwords):
            counts[word % bcfg.num_banks] += 1
        return counts

    def wbuf_read_cycles_per_vector(self, nt: int) -> int:
        if nt <= 0 or nt > self.pe_cfg.num_pe:
            raise ValueError(f"nt must be in [1, {self.pe_cfg.num_pe}], got {nt}")
        bcfg = self.wbuf_cfg.banking
        vector_bits = nt * self.weight_bits
        aggregate_cycles = max(1, ceil(vector_bits / self.wbuf_cfg.read_bits_per_cycle))
        counts = self._bank_word_counts(nt)
        bank_cycles = max(
            max(1, ceil(count / bcfg.read_ports_per_bank))
            for count in counts
        )
        return max(aggregate_cycles, bank_cycles)

    def physical_wbuf_read_bits_per_vector(self, nt: int) -> int:
        bcfg = self.wbuf_cfg.banking
        return sum(self._bank_word_counts(nt)) * bcfg.bank_word_bits

    def compute_cycles(self, tile: WeightTile) -> int:
        service = self.wbuf_read_cycles_per_vector(tile.nt)
        vectors = tile.m_reuse * tile.kt
        if vectors <= 0:
            return self.pe_cfg.startup_cycles_per_tile
        return (
            vectors * service
            + self.wbuf_cfg.banking.read_latency_cycles
            + self.pe_cfg.startup_cycles_per_tile
        )

    def dynamic_energy_pj(self, tile: WeightTile) -> float:
        macs = tile.macs()
        compute = macs * (self.pe_cfg.mac_energy_pj + self.pe_cfg.accumulator_energy_pj)
        physical_read_bits = (
            tile.m_reuse
            * tile.kt
            * self.physical_wbuf_read_bits_per_vector(tile.nt)
        )
        return compute + physical_read_bits * self.wbuf_cfg.read_energy_pj_per_bit
