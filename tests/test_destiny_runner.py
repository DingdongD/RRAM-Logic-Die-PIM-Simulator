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
 - Read Bandwidth  = 4 GB/s
 -  Read Dynamic Energy = 2 nJ
 |--- TSV Dynamic Energy    = 0.3 nJ
"""


def test_destiny_output_is_normalized_without_double_counting_tsv():
    rram, tsv, notes = parse_destiny_read_calibration(CFG, OUT, replicated_banks=64)
    assert rram.num_banks == 64
    assert rram.capacity_bytes == 64 * 1024 * 1024
    assert rram.read_granule_bits == 256
    assert rram.read_latency_ns == pytest.approx(7.0)
    assert rram.read_cycle_time_ns == pytest.approx(8.0)
    assert rram.read_energy_pj_per_access == pytest.approx(1700.0)
    assert tsv.hop_count == 3
    assert tsv.read_latency_ns == pytest.approx(1.0)
    assert tsv.read_energy_pj_per_bit == pytest.approx(300.0 / 3 / 256)
    assert "effective per-data-bit" in notes["tsv_energy_derivation"]


def test_destiny_parser_rejects_missing_tsv_decomposition():
    with pytest.raises(ValueError, match="TSV Latency"):
        parse_destiny_read_calibration(CFG, OUT.replace(" |--- TSV Latency    = 3 ns\n", ""), 64)


def test_destiny_parser_rejects_nonstacked_calibration():
    with pytest.raises(ValueError, match="StackedDieCount"):
        parse_destiny_read_calibration(CFG.replace("-StackedDieCount: 4", "-StackedDieCount: 1"), OUT, 64)
