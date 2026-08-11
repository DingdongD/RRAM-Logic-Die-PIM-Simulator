#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
CAL_DIR="${CAL_DIR:-$ROOT/calibration_runs/destiny_rram_4stack}"
HBM_DIR="${HBM_DIR:-$ROOT/results/ramulator21_smoke}"

cd "$ROOT"

"$PYTHON" -m pip install -e .
"$ROOT/third_party/bootstrap.sh"
"$PYTHON" -m pytest -q

"$PYTHON" "$ROOT/scripts/calibrate_destiny.py" \
  --destiny-dir "$ROOT/third_party/destiny" \
  --config "$ROOT/third_party/configs/destiny_rram_bank_1MiB_256b_4stack.cfg" \
  --output-dir "$CAL_DIR" \
  --replicated-banks 64

MANIFEST="$CAL_DIR/calibration.json"
"$PYTHON" - "$MANIFEST" "$CAL_DIR" <<'PY'
import sys
from rram_cmodel.calibration import DestinyCalibrationManifest
m = DestinyCalibrationManifest.load(sys.argv[1])
m.validate(verify_files=True, base_dir=sys.argv[2])
print("DESTINY calibration manifest verified")
print("RRAM read latency ns:", m.rram.read_latency_ns)
print("RRAM read cycle ns:", m.rram.read_cycle_time_ns)
print("RRAM read energy pJ/access:", m.rram.read_energy_pj_per_access)
print("TSV hops:", m.tsv.hop_count)
print("TSV latency ns/hop:", m.tsv.read_latency_ns)
print("TSV effective pJ/bit/hop:", m.tsv.read_energy_pj_per_bit)
PY

# Small calibrated CModel smoke comparison.
"$PYTHON" -m rram_cmodel.cli compare \
  --M 1 --K 256 --N 256 --weight-bits 8 \
  --destiny-manifest "$MANIFEST" \
  > "$CAL_DIR/compare_smoke.json"

# Small real HBM3 trace through the pinned official Ramulator2.1 backend.
"$PYTHON" -m rram_cmodel.cli hbm-trace \
  --M 1 --K 64 --N 128 --weight-bits 8 \
  --destiny-manifest "$MANIFEST" \
  --workdir "$HBM_DIR" \
  --stem hbm3_smoke \
  > "$HBM_DIR.stdout.json"

# Small DSE smoke to verify the calibrated manifest propagates into sweeps.
"$PYTHON" -m rram_cmodel.cli dse \
  --M 1 --K 256 --N 256 --weight-bits 8 \
  --destiny-manifest "$MANIFEST" \
  --rram-banks 32 64 \
  --tsv-width 256 512 \
  --tsv-fifo-depth 2 8 \
  --wbuf-banks 8 16 \
  --wbuf-kb 16 32 \
  --output "$ROOT/results/dse_smoke.csv"

echo "Full external-backend validation completed."
echo "Calibration: $MANIFEST"
echo "HBM3 run:     $HBM_DIR/hbm3_smoke.run.json"
echo "DSE smoke:    $ROOT/results/dse_smoke.csv"
