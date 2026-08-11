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
_POWER_TO_MW = {"pW": 1e-9, "nW": 1e-6, "uW": 1e-3, "mW": 1.0, "W": 1e3}


def _parse_value_unit(line: str, label: str, units: Dict[str, float], marker: str) -> float | None:
    pat = re.compile(
        rf"^\s*{marker}\s*{re.escape(label)}\s*=\s*"
        rf"([0-9.+\-eE]+)\s*({'|'.join(map(re.escape, units))})\s*$"
    )
    m = pat.match(line)
    if not m:
        return None
    return float(m.group(1)) * units[m.group(2)]


def _exact_quantity(text: str, label: str, units: Dict[str, float]) -> float:
    values = []
    for line in text.splitlines():
        value = _parse_value_unit(line, label, units, r"-")
        if value is not None:
            values.append(value)
    if len(values) != 1:
        raise ValueError(f"Expected exactly one DESTINY '{label}' line, found {len(values)}")
    return values[0]


def _child_quantity(
    text: str,
    parent_label: str,
    child_label: str,
    units: Dict[str, float],
) -> float:
    """Parse one `|--- child` belonging to a specific top-level parent.

    DESTINY reuses labels such as `TSV Latency` under both Read Latency and
    Write Latency. Global label matching is therefore ambiguous. This helper
    walks only the selected top-level result subtree and fails if its child is
    absent or duplicated.
    """

    lines = text.splitlines()
    parent_indices = [
        i for i, line in enumerate(lines)
        if _parse_value_unit(line, parent_label, units, r"-") is not None
    ]
    if len(parent_indices) != 1:
        raise ValueError(
            f"Expected exactly one DESTINY parent '{parent_label}', found {len(parent_indices)}"
        )

    values = []
    for line in lines[parent_indices[0] + 1 :]:
        # The next top-level result line terminates this subtree. Nested lines
        # start with '|---' or deeper indentation and therefore do not match.
        if re.match(r"^\s*-\s+", line):
            break
        value = _parse_value_unit(line, child_label, units, r"\|---")
        if value is not None:
            values.append(value)
    if len(values) != 1:
        raise ValueError(
            f"Expected exactly one DESTINY '{child_label}' under '{parent_label}', "
            f"found {len(values)}"
        )
    return values[0]


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

    DESTINY reports total read latency/energy and an explicit TSV component in
    the same Read subtree for stacked designs. Because the CModel models TSVs
    separately, the RRAM source cost is the total minus that read-path TSV
    component. The parser is section-aware so Read and Write TSV entries cannot
    be confused.

    DESTINY's reported TSV dynamic energy contains the complete TSV read-path
    contribution for one word access (including modeled address/control bits).
    We normalize it to an *effective data-bit* cost that exactly preserves the
    reported per-word TSV energy when the CModel transfers one physical word.
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
    tsv_latency_total_ns = _child_quantity(
        output_text, "Read Latency", "TSV Latency", _TIME_TO_NS
    )
    total_energy_pj = _exact_quantity(output_text, "Read Dynamic Energy", _ENERGY_TO_PJ)
    tsv_energy_total_pj = _child_quantity(
        output_text, "Read Dynamic Energy", "TSV Dynamic Energy", _ENERGY_TO_PJ
    )
    read_bw_bps = _exact_quantity(output_text, "Read Bandwidth", _BW_TO_BPS)
    total_leakage_mw = _exact_quantity(output_text, "Leakage Power", _POWER_TO_MW)
    tsv_leakage_total_mw = _child_quantity(
        output_text, "Leakage Power", "TSV Leakage", _POWER_TO_MW
    )

    rram_latency_ns = total_latency_ns - tsv_latency_total_ns
    rram_energy_pj = total_energy_pj - tsv_energy_total_pj
    rram_leakage_macro_mw = total_leakage_mw - tsv_leakage_total_mw
    if rram_latency_ns <= 0:
        raise ValueError("DESTINY read-path TSV latency is not smaller than total read latency")
    if rram_energy_pj < 0:
        raise ValueError("DESTINY read-path TSV energy exceeds total read dynamic energy")
    if rram_leakage_macro_mw < 0:
        raise ValueError("DESTINY TSV leakage exceeds total leakage power")
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
        # The DESTINY run is treated as one bank macro and then replicated.
        static_power_mw=rram_leakage_macro_mw * replicated_banks,
    )
    tsv = TSVCalibration(
        model_lineage="DESTINY effective-access TSV model derived from CACTI-3DD",
        tsv_type=(
            f"partition={partition};global_projection={global_projection};"
            f"local_projection={local_projection};redundancy={redundancy}"
        ),
        # BankWithHtree calls TSV::Initialize(tsv_type), whose second argument
        # defaults to buffered=false in the pinned DESTINY source.
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
        "rram_latency_derivation": (
            "DESTINY top-level Read Latency - TSV Latency nested under Read Latency"
        ),
        "rram_energy_derivation": (
            "DESTINY top-level Read Dynamic Energy - TSV Dynamic Energy nested under Read Dynamic Energy"
        ),
        "rram_cycle_derivation": "word_bytes / DESTINY Read Bandwidth",
        "rram_static_derivation": (
            "(DESTINY top-level Leakage Power - TSV Leakage nested under Leakage Power) "
            "* replicated_banks"
        ),
        "tsv_latency_derivation": (
            "TSV Latency nested under Read Latency / (StackedDieCount - 1)"
        ),
        "tsv_energy_derivation": (
            "TSV Dynamic Energy nested under Read Dynamic Energy / "
            "(StackedDieCount - 1) / WordWidth; effective per-data-bit cost "
            "preserves DESTINY per-word access energy"
        ),
        "tsv_buffering": (
            "Pinned DESTINY BankWithHtree calls TSV::Initialize(tsv_type) with "
            "default buffered=false"
        ),
        "tsv_area": (
            "not normalized to per-data-lane because DESTINY reports aggregate TSV array area; "
            "area_um2_per_lane remains zero until a lane-count decomposition is added"
        ),
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
