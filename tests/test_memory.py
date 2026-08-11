from dataclasses import replace

from rram_cmodel.components.memory import BankedReadMemory
from rram_cmodel.mapping import BitSegment
from rram_cmodel.presets import functional_example


def test_banks_issue_independently():
    cfg = replace(functional_example().rram, num_banks=2, read_granule_bits=64, read_latency_cycles=5, issue_interval_cycles=4)
    packets, stats = BankedReadMemory(cfg).schedule_read([BitSegment(0, 128)], 0)
    assert stats.accesses == 2
    assert [p.ready_cycle for p in packets] == [5, 5]


def test_same_bank_respects_issue_interval():
    cfg = replace(functional_example().rram, num_banks=1, read_granule_bits=64, read_latency_cycles=5, issue_interval_cycles=4)
    packets, _ = BankedReadMemory(cfg).schedule_read([BitSegment(0, 128)], 0)
    assert [p.ready_cycle for p in packets] == [5, 9]


def test_granule_overfetch_is_counted():
    cfg = replace(functional_example().rram, num_banks=4, read_granule_bits=256)
    _, stats = BankedReadMemory(cfg).schedule_read([BitSegment(4, 4)], 0)
    assert stats.useful_bits == 4
    assert stats.transferred_bits == 256
