# Local calibration and validation pipeline

This is the canonical local reproduction path for the RRAM-logic-die CModel. It uses the same scripts as `.github/workflows/external-backends.yml`.

## 1. Host requirements

Recommended Linux environment:

- Python 3.10+ (3.11 recommended)
- `git`
- `g++`
- `make`
- CMake >= 3.14
- Internet access for the pinned third-party checkout and Ramulator2 CMake dependencies

Ubuntu example:

```bash
sudo apt-get update
sudo apt-get install -y git build-essential cmake python3 python3-venv python3-pip
```

## 2. Clone the simulator branch

```bash
git clone https://github.com/DingdongD/RRAM-Logic-Die-PIM-Simulator.git
cd RRAM-Logic-Die-PIM-Simulator
git fetch origin agent/initial-cycle-cmodel
git checkout agent/initial-cycle-cmodel
```

## 3. Create an isolated Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip pytest
python -m pip install -e .
mkdir -p results calibration_runs
```

## 4. Bootstrap pinned DESTINY + Ramulator2.1

```bash
bash third_party/bootstrap.sh
```

Verify pins and Python ABI:

```bash
git -C third_party/destiny rev-parse HEAD
git -C third_party/ramulator2 rev-parse HEAD
python - <<'PY'
from importlib.metadata import version
from pathlib import Path
from ramulator import _ramulator
print("ramulator package:", version("ramulator"))
print("native extension:", Path(_ramulator.__file__).resolve())
PY
```

Expected revisions are stored in `third_party/versions.json`. `bootstrap.sh` forces Ramulator2 CMake to use the same Python interpreter as the active environment, preventing a native-extension ABI mismatch.

## 5. Run simulator unit/invariant tests

```bash
pytest -q tests
```

The explicit `tests` path is intentional: after bootstrap, `third_party/ramulator2/` contains its own test suites and must not be collected as simulator tests.

## 6. Run a real DESTINY ReRAM/TSV calibration

```bash
python scripts/calibrate_destiny.py \
  --destiny-dir third_party/destiny \
  --config third_party/configs/destiny_rram_bank_1MiB_256b_4stack.cfg \
  --output-dir calibration_runs/destiny_rram_4stack \
  --replicated-banks 64
```

This creates:

```text
calibration_runs/destiny_rram_4stack/
├── destiny.cfg
├── destiny.out
└── calibration.json
```

Validate the archived hashes and normalized parameters:

```bash
python - <<'PY'
from rram_cmodel.calibration import DestinyCalibrationManifest
p = "calibration_runs/destiny_rram_4stack/calibration.json"
m = DestinyCalibrationManifest.load(p)
m.validate(verify_files=True, base_dir="calibration_runs/destiny_rram_4stack")
print(m)
PY
```

For the checked-in reference calibration config, the validated CI run produced approximately:

```text
RRAM read latency     1.795909 ns
RRAM read cycle       1.371801 ns
RRAM read energy      321.526 pJ/access
RRAM static power     316.032 mW for 64 replicated macros
TSV hop count         3
TSV latency           0.00003033 ns/hop
TSV effective energy  0.63275 pJ/bit/hop
```

These are **reference-macro calibration values**, not final silicon claims. Change the DESTINY config for the process, bank capacity, word width, stacking and device assumptions used by an experiment.

## 7. Run a calibrated NPU vs RRAM-NMP smoke comparison

```bash
python -m rram_cmodel.cli compare \
  --M 1 --K 256 --N 256 --weight-bits 8 \
  --destiny-manifest calibration_runs/destiny_rram_4stack/calibration.json \
  | tee results/compare_calibrated.json
```

## 8. Run the exact weight trace through pinned Ramulator2.1 HBM3

```bash
mkdir -p results/ramulator21_smoke
python -m rram_cmodel.cli hbm-trace \
  --M 1 --K 64 --N 128 --weight-bits 8 \
  --destiny-manifest calibration_runs/destiny_rram_4stack/calibration.json \
  --workdir results/ramulator21_smoke \
  --stem hbm3_smoke \
  | tee results/ramulator21_smoke/hbm3_smoke.stdout.json
```

The validated reference smoke run contains 256 ordered HBM transactions and completed in 1101 Ramulator2.1 controller cycles. The run directory contains the exact trace, generated Ramulator runner, run manifest, component configuration, returned `sim.stats`, and hashes of both generated inputs and the installed native `_ramulator` extension.

## 9. Run a small calibrated DSE

```bash
python -m rram_cmodel.cli dse \
  --M 1 --K 256 --N 256 --weight-bits 8 \
  --destiny-manifest calibration_runs/destiny_rram_4stack/calibration.json \
  --rram-banks 32 64 \
  --tsv-width 256 512 \
  --tsv-fifo-depth 2 8 \
  --wbuf-banks 8 16 \
  --wbuf-kb 16 32 \
  --output results/dse_smoke.csv
```

## 10. One-command full validation

After activating the environment, the full path can be executed with:

```bash
bash scripts/run_full_validation.sh
```

This is the same functional pipeline exercised by the `external-backends` GitHub Action: pinned checkout/build, simulator tests, real DESTINY calibration and manifest verification, calibrated compare, real Ramulator2.1 HBM3 trace, and DSE smoke.

A successful run produces the key artifacts:

```text
calibration_runs/destiny_rram_4stack/destiny.cfg
calibration_runs/destiny_rram_4stack/destiny.out
calibration_runs/destiny_rram_4stack/calibration.json
calibration_runs/destiny_rram_4stack/compare_smoke.json
results/ramulator21_smoke/hbm3_smoke.trace
results/ramulator21_smoke/hbm3_smoke_ramulator21.py
results/ramulator21_smoke/hbm3_smoke.run.json
results/ramulator21_smoke/hbm3_smoke.stdout.json
results/dse_smoke.csv
```

## 11. Larger DSE example

```bash
python -m rram_cmodel.cli dse \
  --M 1 --K 4096 --N 4096 --weight-bits 8 \
  --destiny-manifest calibration_runs/destiny_rram_4stack/calibration.json \
  --rram-banks 8 16 32 64 128 \
  --tsv-width 128 256 512 1024 \
  --tsv-fifo-depth 2 4 8 16 \
  --wbuf-banks 8 16 32 \
  --wbuf-kb 8 16 32 64 128 \
  --output results/dse_full.csv
```

## Model-boundary reminder

The cycle/event CModel couples RRAM bank timing, calibrated TSV physical cost, finite FIFO/credits, WBUF bank/port timing, ping-pong buffering, and PE consumption. The official Ramulator2.1 run is currently a source-level HBM reference that returns aggregate controller statistics; the simulator does not fabricate per-request HBM completion timestamps from aggregate cycles. Full Ramulator request-completion coupling is a separate future extension.
