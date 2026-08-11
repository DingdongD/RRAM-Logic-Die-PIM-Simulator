from pathlib import Path

from rram_cmodel.backends.ramulator21 import (
    Ramulator21HBM3Adapter,
    Ramulator21HBM3Config,
)
from rram_cmodel.mapping import BitSegment


def test_ramulator21_defaults_match_upstream_hbm3_components():
    cfg = Ramulator21HBM3Config()
    assert cfg.org_preset == "HBM3_8Gb_8hi"
    assert cfg.timing_preset == "HBM3_6400Mbps"
    assert cfg.scheduler == "FRFCFSRowHit"
    assert cfg.addr_mapper == "RoBaRaCoCh"
    assert cfg.refresh_manager == "HBM34PerBankRefresh"
    assert cfg.transaction_bytes == 32


def test_ramulator21_runner_is_explicit_and_uses_same_trace(tmp_path: Path):
    adapter = Ramulator21HBM3Adapter()
    trace = adapter.write_load_trace(
        [BitSegment(address_bit=0, size_bits=8), BitSegment(address_bit=8, size_bits=8)],
        tmp_path / "x.trace",
    )
    runner = adapter.render_runner(trace, tmp_path / "run.py")
    text = runner.read_text()

    assert trace.read_text().splitlines() == ["LD 0x0", "LD 0x0"]
    assert "ramulator.frontend.LoadStoreTrace" in text
    assert "ramulator.dram.HBM3" in text
    assert "ramulator.controller.HBM34" in text
    assert "HBM3_8Gb_8hi" in text
    assert "HBM3_6400Mbps" in text
    assert str(trace.resolve()) in text


def test_ramulator21_cycle_parser_handles_single_and_multi_channel_stats():
    single = {
        "memory_system": {"controller": {"cycles": 123}}
    }
    multi = {
        "memory_system": {
            "controller": [
                {"id": "Channel 0", "cycles": 100},
                {"id": "Channel 1", "cycles": 140},
            ]
        }
    }
    assert Ramulator21HBM3Adapter._controller_cycles(single) == 123
    assert Ramulator21HBM3Adapter._controller_cycles(multi) == 140
