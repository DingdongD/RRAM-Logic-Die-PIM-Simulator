from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..mapping import BitSegment


@dataclass(frozen=True)
class Ramulator2Run:
    cycles: int
    stdout: str
    trace_path: str
    config_path: str


class Ramulator2Adapter:
    """Strict external Ramulator2 boundary for the Pure-NPU HBM baseline.

    The trace is generated from the exact same bit-address segments used by the
    internal CModel. Each trace line uses Ramulator2 LoadStoreTrace syntax:
    `LD <address>`. `transaction_bytes` must match the DRAM config transaction
    size. No silent fallback to the internal memory model is performed.
    """

    def __init__(self, executable: str, config_template: str, transaction_bytes: int) -> None:
        self.executable = Path(executable)
        self.config_template = Path(config_template)
        self.transaction_bytes = transaction_bytes
        if transaction_bytes <= 0:
            raise ValueError("transaction_bytes must be > 0")

    def _aligned_addresses(self, segments: Sequence[BitSegment]) -> list[int]:
        tx_bits = self.transaction_bytes * 8
        lines = set()
        for seg in segments:
            if seg.address_bit % 8 != 0 or seg.size_bits % 8 != 0:
                raise ValueError(
                    "Ramulator2 trace backend requires byte-addressable segments. "
                    "Pack W4 tiles into bytes or use the internal bit-address backend."
                )
            first = seg.address_bit // tx_bits
            last = (seg.address_bit + seg.size_bits - 1) // tx_bits
            lines.update(range(first, last + 1))
        return [line * self.transaction_bytes for line in sorted(lines)]

    def write_load_trace(self, segments: Sequence[BitSegment], path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        addresses = self._aligned_addresses(segments)
        path.write_text("".join(f"LD 0x{addr:x}\n" for addr in addresses))
        return path

    def render_config(self, trace_path: str | Path, output_path: str | Path) -> Path:
        if not self.config_template.exists():
            raise FileNotFoundError(self.config_template)
        text = self.config_template.read_text()
        token = "${TRACE_PATH}"
        if token not in text:
            raise ValueError(f"Ramulator2 template must contain {token}")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text.replace(token, str(Path(trace_path).resolve())))
        return output_path

    def run(self, trace_path: str | Path, rendered_config_path: str | Path) -> Ramulator2Run:
        if not self.executable.exists():
            raise FileNotFoundError(self.executable)
        rendered_config_path = Path(rendered_config_path)
        if not rendered_config_path.exists():
            raise FileNotFoundError(rendered_config_path)
        proc = subprocess.run(
            [str(self.executable), "-f", str(rendered_config_path)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        cycles = None
        for line in proc.stdout.splitlines():
            if "memory_system_cycles" in line:
                try:
                    cycles = int(float(line.split()[-1]))
                except ValueError:
                    pass
        if cycles is None:
            raise RuntimeError("Ramulator2 output did not contain memory_system_cycles")
        return Ramulator2Run(
            cycles=cycles,
            stdout=proc.stdout,
            trace_path=str(trace_path),
            config_path=str(rendered_config_path),
        )
