from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Sequence

from ..mapping import BitSegment


@dataclass(frozen=True)
class Ramulator2Run:
    cycles: int
    stdout: str
    trace_path: str
    config_path: str
    trace_sha256: str
    config_sha256: str
    transaction_count: int
    parsed_stats: Dict[str, float]

    def write_manifest(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Ramulator2Adapter:
    """Strict external Ramulator2 boundary for the Pure-NPU HBM baseline.

    Trace requests are generated from the exact CModel bit-address segments.
    Request order and repeated transactions are preserved because both affect
    row-buffer state, controller scheduling, and DRAM timing. The adapter never
    silently falls back to the internal bank model.

    The binary invocation follows the CLI used by AttAcc's Ramulator2 fork:
    `<ramulator2> -f <rendered-yaml>`. The template must contain
    `${TRACE_PATH}` and its transaction size must match `transaction_bytes`.
    """

    def __init__(self, executable: str, config_template: str, transaction_bytes: int) -> None:
        self.executable = Path(executable)
        self.config_template = Path(config_template)
        self.transaction_bytes = transaction_bytes
        if transaction_bytes <= 0:
            raise ValueError("transaction_bytes must be > 0")

    def transaction_addresses(self, segments: Sequence[BitSegment]) -> list[int]:
        """Return byte addresses in original request order, without de-duplication.

        Bit-packed W4 segments are legal: every touched DRAM transaction is
        emitted. This models packed storage rather than requiring each logical
        weight to be independently byte-addressable.
        """

        tx_bits = self.transaction_bytes * 8
        addresses: list[int] = []
        for seg in segments:
            if seg.address_bit < 0 or seg.size_bits <= 0:
                raise ValueError("Invalid memory segment")
            first = seg.address_bit // tx_bits
            last = (seg.address_bit + seg.size_bits - 1) // tx_bits
            for line in range(first, last + 1):
                addresses.append(line * self.transaction_bytes)
        return addresses

    # Compatibility alias for callers written against V0.1.
    def _aligned_addresses(self, segments: Sequence[BitSegment]) -> list[int]:
        return self.transaction_addresses(segments)

    def write_load_trace(self, segments: Sequence[BitSegment], path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        addresses = self.transaction_addresses(segments)
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

    @staticmethod
    def _parse_stats(stdout: str) -> Dict[str, float]:
        stats: Dict[str, float] = {}
        # Support both AttAcc-style textual output and standard Ramulator2
        # scalar statistic lines. Unknown fields are ignored rather than
        # guessed; memory_system_cycles is mandatory below.
        names = (
            "memory_system_cycles",
            "total_num_read_requests",
            "total_num_write_requests",
            "avg_read_latency",
            "row_hits",
            "row_misses",
            "row_conflicts",
        )
        for name in names:
            pattern = re.compile(rf"\b{re.escape(name)}\b[^0-9+\-.]*([+\-]?[0-9]+(?:\.[0-9]+)?)")
            matches = pattern.findall(stdout)
            if matches:
                stats[name] = float(matches[-1])
        return stats

    def run(self, trace_path: str | Path, rendered_config_path: str | Path) -> Ramulator2Run:
        if not self.executable.exists():
            raise FileNotFoundError(self.executable)
        rendered_config_path = Path(rendered_config_path)
        trace_path = Path(trace_path)
        if not rendered_config_path.exists():
            raise FileNotFoundError(rendered_config_path)
        if not trace_path.exists():
            raise FileNotFoundError(trace_path)

        proc = subprocess.run(
            [str(self.executable), "-f", str(rendered_config_path)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        stats = self._parse_stats(proc.stdout)
        if "memory_system_cycles" not in stats:
            raise RuntimeError("Ramulator2 output did not contain memory_system_cycles")
        transaction_count = sum(1 for line in trace_path.read_text().splitlines() if line.strip())
        return Ramulator2Run(
            cycles=int(stats["memory_system_cycles"]),
            stdout=proc.stdout,
            trace_path=str(trace_path),
            config_path=str(rendered_config_path),
            trace_sha256=_sha256(trace_path),
            config_sha256=_sha256(rendered_config_path),
            transaction_count=transaction_count,
            parsed_stats=stats,
        )

    def run_segments(
        self,
        segments: Sequence[BitSegment],
        workdir: str | Path,
        stem: str = "hbm_weight_trace",
    ) -> Ramulator2Run:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        trace = self.write_load_trace(segments, workdir / f"{stem}.trace")
        config = self.render_config(trace, workdir / f"{stem}.yaml")
        run = self.run(trace, config)
        run.write_manifest(workdir / f"{stem}.run.json")
        return run
