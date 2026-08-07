from __future__ import annotations

from math import ceil

from ..config import PELineConfig, WBufferConfig
from ..mapping import WeightTile


class PELine:
    """Output-stationary PE-line timing and energy model."""

    def __init__(self, pe_cfg: PELineConfig, wbuf_cfg: WBufferConfig, weight_bits: int) -> None:
        self.pe_cfg = pe_cfg
        self.wbuf_cfg = wbuf_cfg
        self.weight_bits = weight_bits
        lanes_from_wbuf = wbuf_cfg.read_bits_per_cycle // weight_bits
        if lanes_from_wbuf <= 0:
            raise ValueError("WBUF read bandwidth is smaller than one weight")
        self.active_lanes_per_cycle = min(pe_cfg.num_pe, lanes_from_wbuf)

    def compute_cycles(self, tile: WeightTile) -> int:
        lane_groups = ceil(tile.nt / self.active_lanes_per_cycle)
        return tile.m_reuse * tile.kt * lane_groups + self.pe_cfg.startup_cycles_per_tile

    def dynamic_energy_pj(self, tile: WeightTile) -> float:
        macs = tile.macs()
        compute = macs * (self.pe_cfg.mac_energy_pj + self.pe_cfg.accumulator_energy_pj)
        wbuf_read_bits = macs * self.weight_bits
        return compute + wbuf_read_bits * self.wbuf_cfg.read_energy_pj_per_bit
