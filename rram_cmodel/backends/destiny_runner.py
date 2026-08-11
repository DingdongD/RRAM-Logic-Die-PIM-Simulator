from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Dict, Tuple

from ..calibration import RRAMCalibration, TSVCalibration
from .destiny import DestinyManifestBuilder


_TIME_TO_NS = {"ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}
_ENERGY_TO_PJ = {"pJ": 1.0, "nJ": 1e3, "uJ": 1e6, "mJ": 1e9, "J": 1e12}
_BW_TO_BPS = {"B/s": 1.0, "KB/s": 1e3, "MB/s": 1e6, "GB/s": 1e9, "TB/s": 1e12}


def _exact_quantity(text: str, label: str, units: Dict[str, float], tsv: bool = False) -> float:
    marker = r"\|---" if tsv else r"-"
    pat = re.compile(
        rf"(?m)^\s*{marker}\s*{re.escape(label)}\s*=\s*"
        rf"([0-9.+\-eE]+)\s*({'|'.join(map(re.escape, units))})\s*$"
    )
    matches = pat.findall(text)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one DESTINY '{label}' line, found {len(matches)}")
    value, unit = matches[0]
    return float(value) * units[unit]


def _cfg_int(text: str, key: str) -> int:
    pat = re.compile(rf"(?m)^\s*{re.escape(key)}:\s*([0-9]+)\s*$")
    m = pat.search(text)
    if not m:
        raise ValueError(f"Missing DESTINY config field: {key}")
    return int(m.group(1))


def _cfg_float(text: str, key: str) -> float:
    pat = re.compile(rf"(?m)^\s*{re.escape(key)}:\s*([0-9.+\-eE]+)\s*$")
    m = pat.search(text)
    if not m:
        raise ValueError(f"Missing DESTINY config field: {key}")
    return float(m.group(1))


def parse_destiny_read_calibration(
    config_text: str,
    output_text: str,
    replicated_banks: int,
) -> Tuple[RRAMCalibration, TSVCalibration, Dict[str, str]]:
    """Normalize one concrete stacked-ReRAM DESTINY run.

    DESTINY prints total read latency/energy plus an explicit TSV component for
    stacked designs. The CModel counts the TSV separately, so the RRAM source
    cost is the reported total minus the reported TSV component. This function
    fails if the decomposition is absent or inconsistent; it never guesses a
    unit or silently falls back to a total-memory number.

    DESTINY's reported TSV dynamic energy contains the complete TSV read-path
    contribution for one word access (including the model's address/control
    contribution). We therefore convert it to an *effective data-bit* cost by
    dividing by word width and hop count. This preserves the exact DESTINY
    per-access TSV energy when one physical read granule is transferred.
    """
    if replicated_banks <= 0:
        raise ValueError("replicated_banks must be > 0")

    capacity_mib = _cfg_int(config_text, "-Capacity (MB)")
    word_bits = _cfg_int(config_text, "-WordWidth (bit)")
    stacked_dies = _cfg_int(config_text, "-StackedDieCount")
    partition = _cfg_int(config_text, "-PartitionGranularity")
    global_projection = _cfg_int(config_text, "-GlobalTSVProjection")
    local_projection = _cfg_int(config_text, "-LocalTSVProjection")
    redundancy = _cfg_float(config_text, "-TSVRedundancy")

    hop_count = stacked_dies - 1
    if hop_count <= 0:
        raise ValueError("Calibration requires StackedDieCount > 1 so TSV decomposition exists")

    total_latency_ns = _exact_quantity(output_text, "Read Latency", _TIME_TO_NS)
    tsv_latency_total_ns = _exact_quantity(output_text, "TSV Latency", _TIME_TO_NS, tsv=True)
    total_energy_pj = _exact_quantity(output_text, "Read Dynamic Energy", _ENERGY_TO_PJ)
    tsv_energy_total_pj = _exact_quantity(output_text, "TSV Dynamic Energy", _ENERGY_TO_PJ, tsv=True)
    read_bw_bps = _exact_quantity(output_text, "Read Bandwidth", _BW_TO_BPS)

    rram_latency_ns = total_latency_ns - tsv_latency_total_ns
    rram_energy_pj = total_energy_pj - tsv_energy_total_pj
    if rram_latency_ns <= 0:
        raise ValueError("DESTINY TSV latency is not smaller than total read latency")
    if rram_energy_pj < 0:
        raise ValueError("DESTINY TSV energy exceeds total read dynamic energy")
    if read_bw_bps <= 0:
        raise ValueError("DESTINY read bandwidth must be > 0")

    read_cycle_time_ns = (word_bits / 8.0) / read_bw_bps * 1e9
    macro_capacity = capacity_mib * 1024 * 1024

    rram = RRAMCalibration(
        num_banks=replicated_banks,
        capacity_bytes=macro_capacity * replicated_banks,
        read_granule_bits=word_bits,
        read_latency_ns=rram_latency_ns,
        read_cycle_time_ns=read_cycle_time_ns,
        read_energy_pj_per_access=rram_energy_pj,
        static_power_mw=0.0,
    )
    tsv = TSVCalibration(
        model_lineage="DESTINY effective-access TSV model derived from CACTI-3DD",
        tsv_type=(
            f"partition={partition};global_projection={global_projection};"
            f"local_projection={local_projection};redundancy={redundancy}"
        ),
        buffered=False,
        read_latency_ns=tsv_latency_total_ns / hop_count,
        read_energy_pj_per_bit=tsv_energy_total_pj / hop_count / word_bits,
        area_um2_per_lane=0.0,
        resistance_ohm=None,
        capacitance_ff=None,
        hop_count=hop_count,
    )
    notes = {
        "macro_replication": (
            f"DESTINY config models one {capacity_mib} MiB, {word_bits}-bit bank macro; "
            f"CModel replicates it {replicated_banks} times"
        ),
        "rram_latency_derivation": "DESTINY Read Latency - explicit TSV Latency",
        "rram_energy_derivation": "DESTINY Read Dynamic Energy - explicit TSV Dynamic Energy",
        "rram_cycle_derivation": "word_bytes / DESTINY Read Bandwidth",
        "tsv_latency_derivation": "explicit TSV Latency / (StackedDieCount - 1)",
        "tsv_energy_derivation": (
            "explicit TSV Dynamic Energy / (StackedDieCount - 1) / WordWidth; "
            "effective per-data-bit cost preserves DESTINY per-word access energy"
        ),
        "tsv_buffering": "DESTINY BankWithHtree calls TSV::Initialize(tsv_type) with default buffered=false",
        "static_power": "not normalized by this calibration path; set to zero",
    }
    return rram, tsv, notes


def run_destiny_calibration(
    destiny_dir: str | Path,
    config_template: str | Path,
    output_dir: str | Path,
    replicated_banks: int = 64,
) -> Path:
    destiny_dir = Path(destiny_dir).resolve()
    config_template = Path(config_template).resolve()
    output_dir = Path(output_dir).resolve()
    binary = destiny_dir / "destiny"
    config_dir = destiny_dir / "config"
    if not binary.exists():
        raise FileNotFoundError(f"DESTINY binary not found: {binary}")
    if not config_template.exists():
        raise FileNotFoundError(config_template)
    if not (config_dir / "sample_RRAM.cell").exists():
        raise FileNotFoundError(config_dir / "sample_RRAM.cell")

    output_dir.mkdir(parents=True, exist_ok=True)
    run_cfg = config_dir / "rram_cmodel_calibration.cfg"
    shutil.copy2(config_template, run_cfg)

    proc = subprocess.run(
        [str(binary), run_cfg.name],
        cwd=config_dir,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    archived_cfg = output_dir / "destiny.cfg"
    raw_output = output_dir / "destiny.out"
    shutil.copy2(run_cfg, archived_cfg)
    raw_output.write_text(proc.stdout)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=destiny_dir,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    rram, tsv, notes = parse_destiny_read_calibration(
        archived_cfg.read_text(), raw_output.read_text(), replicated_banks
    )
    manifest = DestinyManifestBuilder.build(
        destiny_commit=commit,
        config_path=archived_cfg,
        raw_output_path=raw_output,
        rram=rram,
        tsv=tsv,
        notes=notes,
    )
    # Store artifact-local paths so the calibration directory is relocatable.
    manifest = replace(
        manifest,
        provenance=replace(
            manifest.provenance,
            config_path=archived_cfg.name,
            raw_output_path=raw_output.name,
        ),
    )
    manifest_path = manifest.write(output_dir / "calibration.json")
    manifest.validate(verify_files=True, base_dir=output_dir)
    return manifest_path
