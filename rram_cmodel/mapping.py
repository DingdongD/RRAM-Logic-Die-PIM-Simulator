from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .config import PELineConfig, WBufferConfig
from .workload import GemmWorkload


@dataclass(frozen=True)
class BitSegment:
    address_bit: int
    size_bits: int


@dataclass(frozen=True)
class WeightTile:
    tile_id: int
    n0: int
    k0: int
    nt: int
    kt: int
    m_reuse: int

    def useful_weight_bits(self, workload: GemmWorkload) -> int:
        return self.kt * self.nt * workload.weight_bits

    def macs(self) -> int:
        return self.m_reuse * self.kt * self.nt

    def weight_segments(self, workload: GemmWorkload) -> List[BitSegment]:
        """Exact row-major W[K,N] bit ranges for this tile.

        A tile with nt < N is physically strided. Treating Kt x Nt as one
        contiguous blob would distort bank conflicts and read-granule overfetch.
        """
        out: List[BitSegment] = []
        for kk in range(self.kt):
            linear_elem = (self.k0 + kk) * workload.N + self.n0
            out.append(
                BitSegment(
                    address_bit=linear_elem * workload.weight_bits,
                    size_bits=self.nt * workload.weight_bits,
                )
            )
        return out


class GemmMapper:
    """Deterministic output-channel mapping onto a 1-D PE line.

    V0.1 intentionally requires M <= psum_contexts. This avoids silently
    spilling partial sums or refetching weights. Those behaviors should be
    explicit future mapping policies, not hidden approximations.
    """

    def __init__(self, workload: GemmWorkload, pe: PELineConfig, wbuf: WBufferConfig) -> None:
        self.workload = workload
        self.pe = pe
        self.wbuf = wbuf
        if workload.M > pe.psum_contexts:
            raise ValueError(
                f"M={workload.M} exceeds psum_contexts={pe.psum_contexts}; "
                "V0.1 does not model PSUM spill/refetch implicitly."
            )

    def _max_kt(self, nt: int) -> int:
        bits_per_k = nt * self.workload.weight_bits
        kt = self.wbuf.half_capacity_bits // bits_per_k
        if kt <= 0:
            raise ValueError("One K-row of the weight tile does not fit in a WBUF half")
        return min(kt, self.workload.K)

    def tiles(self) -> List[WeightTile]:
        tiles: List[WeightTile] = []
        tid = 0
        for n0 in range(0, self.workload.N, self.pe.num_pe):
            nt = min(self.pe.num_pe, self.workload.N - n0)
            max_kt = self._max_kt(nt)
            for k0 in range(0, self.workload.K, max_kt):
                kt = min(max_kt, self.workload.K - k0)
                tile = WeightTile(
                    tile_id=tid,
                    n0=n0,
                    k0=k0,
                    nt=nt,
                    kt=kt,
                    m_reuse=self.workload.M,
                )
                if tile.useful_weight_bits(self.workload) > self.wbuf.half_capacity_bits:
                    raise AssertionError("Mapper emitted an oversized WBUF tile")
                tiles.append(tile)
                tid += 1
        return tiles
