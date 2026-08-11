from dataclasses import replace

import pytest

from rram_cmodel.components.compute import PELine
from rram_cmodel.components.memory import (
    BankedReadMemory,
    BankedWBufferWriteStage,
    Packet,
    RRAMTSVWBufferPath,
)
from rram_cmodel.config import FIFOConfig, SystemConfig
from rram_cmodel.mapping import BitSegment, WeightTile
from rram_cmodel.presets import functional_example


def _slow_wbuf(base, *, banks: int, write_ports: int = 1):
    banking = replace(
        base.wbuf.banking,
        num_banks=banks,
        bank_word_bits=64,
        write_ports_per_bank=write_ports,
        read_ports_per_bank=1,
        write_latency_cycles=1,
    )
    return replace(base.wbuf, banking=banking)


def test_wbuf_write_bank_parallelism_reduces_packet_completion():
    base = functional_example()
    p = Packet(index=0, ready_cycle=0, bits=256)

    one_bank = BankedWBufferWriteStage(_slow_wbuf(base, banks=1))
    four_banks = BankedWBufferWriteStage(_slow_wbuf(base, banks=4))

    q1, s1 = one_bank.schedule_packet(p)
    q4, s4 = four_banks.schedule_packet(p)

    assert q4.ready_cycle < q1.ready_cycle
    assert s1.transferred_bits == s4.transferred_bits == 256
    assert s1.accesses == s4.accesses == 4


def test_wbuf_read_ports_limit_pe_service_rate():
    base = functional_example()
    one_bank = _slow_wbuf(base, banks=1)
    sixteen_banks = _slow_wbuf(base, banks=16)

    pe1 = PELine(base.pe, one_bank, weight_bits=8)
    pe16 = PELine(base.pe, sixteen_banks, weight_bits=8)
    tile = WeightTile(tile_id=0, n0=0, k0=0, nt=128, kt=8, m_reuse=1)

    assert pe16.compute_cycles(tile) < pe1.compute_cycles(tile)


def test_finite_fifo_backpressure_is_monotonic_and_observable():
    base = functional_example()
    wbuf = _slow_wbuf(base, banks=1)
    source_cfg = replace(
        base.rram,
        num_banks=32,
        read_granule_bits=256,
        read_latency_cycles=1,
        issue_interval_cycles=1,
    )
    tsv = replace(base.tsv, width_bits_per_cycle=256, latency_cycles=0)
    segments = [BitSegment(address_bit=0, size_bits=256 * 8)]

    shallow = RRAMTSVWBufferPath(
        BankedReadMemory(source_cfg),
        tsv,
        FIFOConfig(depth_entries=1, packet_bits=256),
        wbuf,
    ).fetch(segments, 0)
    deep = RRAMTSVWBufferPath(
        BankedReadMemory(source_cfg),
        tsv,
        FIFOConfig(depth_entries=8, packet_bits=256),
        wbuf,
    ).fetch(segments, 0)

    assert shallow.ready_cycle >= deep.ready_cycle
    assert shallow.stages["tsv"].queue_stall_cycles > 0
    assert shallow.stages["tsv"].max_queue_occupancy <= 1
    assert deep.stages["tsv"].max_queue_occupancy <= 8


def test_system_rejects_implicit_tsv_repacketization():
    base = functional_example()
    with pytest.raises(ValueError, match="repacketizer"):
        SystemConfig(
            clock=base.clock,
            pe=base.pe,
            wbuf=base.wbuf,
            rram=replace(base.rram, read_granule_bits=512),
            tsv=base.tsv,
            hbm=base.hbm,
            npu_direct_stages=base.npu_direct_stages,
            npu_hierarchical_stages=base.npu_hierarchical_stages,
            tsv_fifo=FIFOConfig(depth_entries=8, packet_bits=256),
            metadata=base.metadata,
        )
