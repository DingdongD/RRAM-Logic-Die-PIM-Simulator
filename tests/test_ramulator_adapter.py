from pathlib import Path

from rram_cmodel.backends.ramulator2 import Ramulator2Adapter
from rram_cmodel.mapping import BitSegment


def test_ramulator_trace_uses_exact_aligned_transactions(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text("Frontend:\n  impl: LoadStoreTrace\n  path: ${TRACE_PATH}\n")
    adapter = Ramulator2Adapter("/nonexistent", str(template), transaction_bytes=32)
    trace = adapter.write_load_trace(
        [BitSegment(address_bit=0, size_bits=8), BitSegment(address_bit=256, size_bits=8)],
        tmp_path / "x.trace",
    )
    assert trace.read_text().splitlines() == ["LD 0x0", "LD 0x20"]
    rendered = adapter.render_config(trace, tmp_path / "run.yaml")
    assert "${TRACE_PATH}" not in rendered.read_text()


def test_ramulator_trace_preserves_order_and_repeated_transactions(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text("path: ${TRACE_PATH}\n")
    adapter = Ramulator2Adapter("/nonexistent", str(template), transaction_bytes=32)

    # Both small segments touch transaction 0 and must remain two requests.
    # The third request then returns to transaction 0 after touching 0x20.
    segments = [
        BitSegment(address_bit=0, size_bits=8),
        BitSegment(address_bit=256, size_bits=8),
        BitSegment(address_bit=8, size_bits=8),
    ]
    trace = adapter.write_load_trace(segments, tmp_path / "ordered.trace")
    assert trace.read_text().splitlines() == ["LD 0x0", "LD 0x20", "LD 0x0"]


def test_ramulator_trace_accepts_bit_packed_w4_segments(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text("path: ${TRACE_PATH}\n")
    adapter = Ramulator2Adapter("/nonexistent", str(template), transaction_bytes=32)
    trace = adapter.write_load_trace(
        [BitSegment(address_bit=4, size_bits=4)],
        tmp_path / "w4.trace",
    )
    assert trace.read_text().splitlines() == ["LD 0x0"]
