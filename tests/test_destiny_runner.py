import pytest

from rram_cmodel.backends.destiny_runner import parse_destiny_read_calibration


CFG = """
-Capacity (MB): 1
-WordWidth (bit): 256
-StackedDieCount: 4
-PartitionGranularity: 1
-LocalTSVProjection: 0
-GlobalTSVProjection: 0
-TSVRedundancy: 1.0
"""

OUT = """
 -  Read Latency = 10 ns
 |--- TSV Latency    = 3 ns
 |--- H-Tree Latency = 1 ns
 - Write Latency = 20 ns
 |--- TSV Latency    = 6 ns
 - Read Bandwidth  = 4 GB/s
 - Write Bandwidth = 2 GB/s
 -  Read Dynamic Energy = 2 nJ
 |--- TSV Dynamic Energy    = 0.3 nJ
 - Write Dynamic Energy = 4 nJ
 |--- TSV Dynamic Energy    = 0.8 nJ
 - Leakage Power = 5 mW
 |--- TSV Leakage              = 0.5 mW
"""


def test_destiny_output_is_normalized_without_double_counting_tsv():
    rram, tsv, notes = parse_destiny_read_calibration(CFG, OUT, replicated_banks=64)
    assert rram.num_banks == 64
    assert rram.capacity_bytes == 64 * 1024 * 1024
    assert rram.read_granule_bits == 256
    assert rram.read_latency_ns == pytest.approx(7.0)
    assert rram.read_cycle_time_ns == pytest.approx(8.0)
    assert rram.read_energy_pj_per_access == pytest.approx(1700.0)
    assert rram.static_power_mw == pytest.approx((5.0 - 0.5) * 64)
    assert tsv.hop_count == 3
    assert tsv.read_latency_ns == pytest.approx(1.0)
    assert tsv.read_energy_pj_per_bit == pytest.approx(300.0 / 3 / 256)
    assert "effective per-data-bit" in notes["tsv_energy_derivation"]


def test_destiny_parser_uses_read_child_not_write_child():
    rram, tsv, _ = parse_destiny_read_calibration(CFG, OUT, replicated_banks=1)
    assert rram.read_latency_ns == pytest.approx(7.0)
    assert tsv.read_latency_ns == pytest.approx(1.0)
    assert rram.read_energy_pj_per_access == pytest.approx(1700.0)


def test_destiny_parser_rejects_missing_read_tsv_decomposition():
    bad = OUT.replace(" |--- TSV Latency    = 3 ns\n", "", 1)
    with pytest.raises(ValueError, match="TSV Latency.*Read Latency"):
        parse_destiny_read_calibration(CFG, bad, 64)


def test_destiny_parser_rejects_nonstacked_calibration():
    with pytest.raises(ValueError, match="StackedDieCount"):
        parse_destiny_read_calibration(CFG.replace("-StackedDieCount: 4", "-StackedDieCount: 1"), OUT, 64)
