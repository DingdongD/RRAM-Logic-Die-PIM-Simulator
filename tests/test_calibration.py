from dataclasses import replace
from pathlib import Path

import pytest

from rram_cmodel.calibration import RRAMCalibration, TSVCalibration
from rram_cmodel.backends.destiny import DestinyManifestBuilder
from rram_cmodel.presets import functional_example


def _rram():
    return RRAMCalibration(
        num_banks=64,
        capacity_bytes=128 * 1024 * 1024,
        read_granule_bits=256,
        read_latency_ns=7.1,
        read_cycle_time_ns=2.2,
        read_energy_pj_per_access=4.5,
        static_power_mw=12.0,
    )


def _tsv():
    return TSVCalibration(
        model_lineage="DESTINY TSV model derived from CACTI-3DD",
        tsv_type="Fine",
        buffered=True,
        read_latency_ns=0.42,
        read_energy_pj_per_bit=0.015,
        area_um2_per_lane=25.0,
        resistance_ohm=0.2,
        capacitance_ff=30.0,
    )


def test_destiny_manifest_round_trip_and_apply(tmp_path: Path):
    cfg_file = tmp_path / "destiny.cfg"
    raw_file = tmp_path / "destiny.out"
    cfg_file.write_text("-MemoryType: data\n")
    raw_file.write_text("Read Latency = 7.1e-9\n")

    manifest = DestinyManifestBuilder.build(
        destiny_commit="32ef839f9f32484a7457f8013b2a0883e757300b",
        config_path=cfg_file,
        raw_output_path=raw_file,
        rram=_rram(),
        tsv=_tsv(),
    )
    path = manifest.write(tmp_path / "calibration.json")
    loaded = type(manifest).load(path)
    loaded.validate(verify_files=True)

    calibrated = loaded.apply(functional_example())
    assert calibrated.rram.num_banks == 64
    assert calibrated.rram.read_latency_cycles == 8
    assert calibrated.rram.issue_interval_cycles == 3
    assert calibrated.tsv.latency_cycles == 1
    assert calibrated.tsv.energy_pj_per_bit == pytest.approx(0.015)
    assert calibrated.tsv.aggregate_area_um2 == pytest.approx(
        calibrated.tsv.width_bits_per_cycle * 25.0
    )
    assert calibrated.metadata["calibration"] == "destiny_manifest_v1"
    assert calibrated.metadata["destiny_commit"] == manifest.provenance.commit


def test_manifest_detects_modified_raw_output(tmp_path: Path):
    cfg_file = tmp_path / "destiny.cfg"
    raw_file = tmp_path / "destiny.out"
    cfg_file.write_text("cfg-v1")
    raw_file.write_text("raw-v1")
    manifest = DestinyManifestBuilder.build(
        destiny_commit="deadbeef",
        config_path=cfg_file,
        raw_output_path=raw_file,
        rram=_rram(),
        tsv=_tsv(),
    )
    raw_file.write_text("raw-v2")
    with pytest.raises(ValueError, match="raw-output SHA256 mismatch"):
        manifest.validate(verify_files=True)


def test_manifest_rejects_unidentified_tsv_model_lineage(tmp_path: Path):
    cfg_file = tmp_path / "destiny.cfg"
    raw_file = tmp_path / "destiny.out"
    cfg_file.write_text("cfg")
    raw_file.write_text("raw")
    bad_tsv = replace(_tsv(), model_lineage="unknown custom TSV model")
    with pytest.raises(ValueError, match="CACTI-3DD"):
        DestinyManifestBuilder.build(
            destiny_commit="deadbeef",
            config_path=cfg_file,
            raw_output_path=raw_file,
            rram=_rram(),
            tsv=bad_tsv,
        )
