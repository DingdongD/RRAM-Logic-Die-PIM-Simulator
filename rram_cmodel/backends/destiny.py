from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Dict, Mapping

from ..config import BankedMemoryConfig


class DestinyParameterAdapter:
    """Strict adapter boundary for DESTINY-derived RRAM macro parameters.

    DESTINY versions/configurations do not expose one universal machine-readable
    schema. Therefore this adapter intentionally does not guess field names.
    Callers provide semantic-field regexes, and parsing fails if required data
    is absent.
    """

    REQUIRED = ("read_latency_ns", "read_energy_pj_per_access")

    @staticmethod
    def parse_text(path: str | Path, patterns: Mapping[str, str]) -> Dict[str, float]:
        text = Path(path).read_text()
        out: Dict[str, float] = {}
        for field in DestinyParameterAdapter.REQUIRED:
            if field not in patterns:
                raise ValueError(f"Missing regex mapping for {field}")
            match = re.search(patterns[field], text, flags=re.MULTILINE)
            if not match:
                raise ValueError(f"Could not parse {field} from {path}")
            out[field] = float(match.group(1))
        return out

    @staticmethod
    def apply(
        base: BankedMemoryConfig,
        parsed: Mapping[str, float],
        pe_period_ns: float,
    ) -> BankedMemoryConfig:
        if pe_period_ns <= 0:
            raise ValueError("pe_period_ns must be > 0")
        latency_cycles = max(1, math.ceil(parsed["read_latency_ns"] / pe_period_ns))
        return replace(
            base,
            read_latency_cycles=latency_cycles,
            read_energy_pj_per_access=parsed["read_energy_pj_per_access"],
        )
