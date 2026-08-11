from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Dict, Mapping

from ..calibration import (
    DestinyCalibrationManifest,
    RRAMCalibration,
    TSVCalibration,
    ToolProvenance,
    sha256_file,
)
from ..config import BankedMemoryConfig, SystemConfig


class DestinyParameterAdapter:
    """Strict legacy adapter for extracting normalized scalar fields.

    DESTINY configurations do not expose one universal machine-readable output
    schema. This adapter therefore never guesses labels or units: callers must
    provide the exact regex and an explicit numeric scale for every semantic
    field. For publication experiments prefer `DestinyManifestBuilder`, which
    records tool commit and source-file hashes.
    """

    REQUIRED = ("read_latency_ns", "read_energy_pj_per_access")

    @staticmethod
    def parse_text(
        path: str | Path,
        patterns: Mapping[str, str],
        scales: Mapping[str, float] | None = None,
    ) -> Dict[str, float]:
        text = Path(path).read_text()
        scales = scales or {}
        out: Dict[str, float] = {}
        for field in DestinyParameterAdapter.REQUIRED:
            if field not in patterns:
                raise ValueError(f"Missing regex mapping for {field}")
            match = re.search(patterns[field], text, flags=re.MULTILINE)
            if not match:
                raise ValueError(f"Could not parse {field} from {path}")
            out[field] = float(match.group(1)) * float(scales.get(field, 1.0))
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


class DestinyManifestBuilder:
    """Build immutable normalized manifests from one concrete DESTINY run.

    Numeric values supplied here must already be normalized to the units in the
    dataclasses. This is deliberate: DESTINY's human-readable formatter can
    change prefixes, while the manifest must not infer or guess units.
    """

    @staticmethod
    def build(
        *,
        destiny_commit: str,
        config_path: str | Path,
        raw_output_path: str | Path,
        rram: RRAMCalibration,
        tsv: TSVCalibration,
        notes: Mapping[str, str] | None = None,
        repository: str = "https://github.com/sparsh0mittal/destiny_3d_cache",
    ) -> DestinyCalibrationManifest:
        config_path = Path(config_path)
        raw_output_path = Path(raw_output_path)
        if not config_path.exists():
            raise FileNotFoundError(config_path)
        if not raw_output_path.exists():
            raise FileNotFoundError(raw_output_path)
        manifest = DestinyCalibrationManifest(
            schema_version=1,
            provenance=ToolProvenance(
                name="DESTINY",
                repository=repository,
                commit=destiny_commit,
                config_path=str(config_path),
                config_sha256=sha256_file(config_path),
                raw_output_path=str(raw_output_path),
                raw_output_sha256=sha256_file(raw_output_path),
            ),
            rram=rram,
            tsv=tsv,
            notes=dict(notes or {}),
        )
        manifest.validate()
        return manifest

    @staticmethod
    def load_and_apply(manifest_path: str | Path, base: SystemConfig) -> SystemConfig:
        return DestinyCalibrationManifest.load(manifest_path).apply(base)
