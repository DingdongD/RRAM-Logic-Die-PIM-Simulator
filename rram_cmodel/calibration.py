from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Dict, Optional

from .config import SystemConfig


def sha256_file(path: str | Path) -> str:
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ToolProvenance:
    name: str
    repository: str
    commit: str
    config_path: str
    config_sha256: str
    raw_output_path: str
    raw_output_sha256: str


@dataclass(frozen=True)
class RRAMCalibration:
    num_banks: int
    capacity_bytes: int
    read_granule_bits: int
    read_latency_ns: float
    read_cycle_time_ns: float
    read_energy_pj_per_access: float
    static_power_mw: float = 0.0

    def validate(self) -> None:
        if self.num_banks <= 0 or self.capacity_bytes <= 0 or self.read_granule_bits <= 0:
            raise ValueError("RRAM organization values must be > 0")
        if self.read_latency_ns <= 0 or self.read_cycle_time_ns <= 0:
            raise ValueError("RRAM timing values must be > 0")
        if self.read_energy_pj_per_access < 0 or self.static_power_mw < 0:
            raise ValueError("RRAM energy/power values must be >= 0")


@dataclass(frozen=True)
class TSVCalibration:
    """Per-data-TSV physical calibration.

    `read_latency_ns` is propagation/driver delay for one modeled TSV hop.
    `read_energy_pj_per_bit` is the dynamic energy of one data bit traversing
    that hop. Aggregate data-lane count remains an architecture parameter in
    `SystemConfig.tsv.width_bits_per_cycle`.
    """

    model_lineage: str
    tsv_type: str
    buffered: bool
    read_latency_ns: float
    read_energy_pj_per_bit: float
    area_um2_per_lane: float
    resistance_ohm: Optional[float] = None
    capacitance_ff: Optional[float] = None

    def validate(self) -> None:
        if self.read_latency_ns < 0:
            raise ValueError("TSV latency must be >= 0")
        if self.read_energy_pj_per_bit < 0 or self.area_um2_per_lane < 0:
            raise ValueError("TSV energy/area must be >= 0")
        if self.resistance_ohm is not None and self.resistance_ohm < 0:
            raise ValueError("TSV resistance must be >= 0")
        if self.capacitance_ff is not None and self.capacitance_ff < 0:
            raise ValueError("TSV capacitance must be >= 0")


@dataclass(frozen=True)
class DestinyCalibrationManifest:
    schema_version: int
    provenance: ToolProvenance
    rram: RRAMCalibration
    tsv: TSVCalibration
    notes: Dict[str, str] = field(default_factory=dict)

    def validate(self, verify_files: bool = False, base_dir: str | Path | None = None) -> None:
        if self.schema_version != 1:
            raise ValueError(f"Unsupported calibration schema {self.schema_version}")
        if self.provenance.name.upper() != "DESTINY":
            raise ValueError("DestinyCalibrationManifest requires tool name DESTINY")
        if not self.provenance.commit:
            raise ValueError("DESTINY commit must be recorded")
        self.rram.validate()
        self.tsv.validate()
        if "CACTI-3DD" not in self.tsv.model_lineage:
            raise ValueError(
                "TSV model_lineage must explicitly identify the CACTI-3DD lineage "
                "used by DESTINY"
            )
        if verify_files:
            root = Path(base_dir or ".")
            cfg = root / self.provenance.config_path
            raw = root / self.provenance.raw_output_path
            if sha256_file(cfg) != self.provenance.config_sha256:
                raise ValueError("DESTINY config SHA256 mismatch")
            if sha256_file(raw) != self.provenance.raw_output_sha256:
                raise ValueError("DESTINY raw-output SHA256 mismatch")

    def write(self, path: str | Path) -> Path:
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        return path

    @staticmethod
    def load(path: str | Path) -> "DestinyCalibrationManifest":
        data = json.loads(Path(path).read_text())
        manifest = DestinyCalibrationManifest(
            schema_version=int(data["schema_version"]),
            provenance=ToolProvenance(**data["provenance"]),
            rram=RRAMCalibration(**data["rram"]),
            tsv=TSVCalibration(**data["tsv"]),
            notes=dict(data.get("notes", {})),
        )
        manifest.validate()
        return manifest

    def apply(self, base: SystemConfig) -> SystemConfig:
        """Apply normalized DESTINY calibration to an architectural config."""

        self.validate()
        period_ns = base.clock.period_ns
        rram = replace(
            base.rram,
            num_banks=self.rram.num_banks,
            capacity_bytes=self.rram.capacity_bytes,
            read_granule_bits=self.rram.read_granule_bits,
            read_latency_cycles=max(1, math.ceil(self.rram.read_latency_ns / period_ns)),
            issue_interval_cycles=max(1, math.ceil(self.rram.read_cycle_time_ns / period_ns)),
            read_energy_pj_per_access=self.rram.read_energy_pj_per_access,
            static_power_mw=self.rram.static_power_mw,
        )
        provenance = (
            f"DESTINY@{self.provenance.commit}; "
            f"{self.tsv.model_lineage}; type={self.tsv.tsv_type}; "
            f"buffered={self.tsv.buffered}"
        )
        tsv = replace(
            base.tsv,
            latency_cycles=max(0, math.ceil(self.tsv.read_latency_ns / period_ns)),
            energy_pj_per_bit=self.tsv.read_energy_pj_per_bit,
            area_um2_per_lane=self.tsv.area_um2_per_lane,
            provenance=provenance,
        )
        metadata = dict(base.metadata)
        metadata.update(
            {
                "calibration": "destiny_manifest_v1",
                "destiny_commit": self.provenance.commit,
                "destiny_config_sha256": self.provenance.config_sha256,
                "destiny_output_sha256": self.provenance.raw_output_sha256,
                "tsv_model_lineage": self.tsv.model_lineage,
            }
        )
        return replace(base, rram=rram, tsv=tsv, metadata=metadata)
