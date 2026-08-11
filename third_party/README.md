# External modeling backends

This directory is the reproducible boundary for external physical/memory models. Source trees are **not copied into git**; `bootstrap.sh` checks out the exact revisions in `versions.json`, verifies the commit IDs, builds them, and installs the pinned Ramulator2 Python package.

```bash
bash third_party/bootstrap.sh
```

Generated source trees live under `third_party/destiny/` and `third_party/ramulator2/` and are ignored by git.

## DESTINY / ReRAM + TSV physical calibration

- Repository: `sparsh0mittal/destiny_3d_cache`
- Pinned revision: `32ef839f9f32484a7457f8013b2a0883e757300b`
- Relevant sources: `TSV.h`, `TSV.cpp`, `Result.cpp`, `macros.h`

DESTINY states that its coarse/fine TSV models come from CACTI-3DD. Its `BankWithHtree` initializes the TSV with `TSV::Initialize(tsv_type)` (the default unbuffered electrical model); `TSV.cpp` obtains TSV R/C/area from technology parameters and computes propagation delay/dynamic energy. `Result.cpp` separately reports total read latency/energy, TSV latency/energy, and read bandwidth for stacked configurations.

The checked-in reference calibration input is:

```text
third_party/configs/destiny_rram_bank_1MiB_256b_4stack.cfg
```

It is intentionally a **calibration macro**, not a claim that the final architecture must use 180 nm, 1 MiB banks, four memory dies, or 64 replicated banks. Those settings are explicit and can be replaced by a paper-specific configuration.

Run a real calibration:

```bash
python scripts/calibrate_destiny.py \
  --replicated-banks 64 \
  --output-dir calibration_runs/destiny_rram_4stack
```

The runner archives the exact config and raw DESTINY stdout, verifies the checked-out DESTINY commit, and creates `calibration.json`. Normalization is strict:

- `RRAM read latency = reported Read Latency - reported TSV Latency`.
- `RRAM read energy/access = reported Read Dynamic Energy - reported TSV Dynamic Energy`.
- `RRAM issue interval = word_bytes / reported Read Bandwidth`.
- `TSV latency/hop = reported TSV Latency / (StackedDieCount - 1)`.
- `TSV effective energy/bit/hop = reported TSV Dynamic Energy / (StackedDieCount - 1) / WordWidth`.

The final TSV term is explicitly an **effective per-data-bit access cost**: DESTINY's reported TSV term contains the complete TSV contribution for the word access, including its modeled address/control component. This normalization preserves the exact DESTINY TSV energy for one physical word transfer without pretending that DESTINY printed a pure data-via-only number.

The parser fails if the total/TSV decomposition is absent, inconsistent, negative after subtraction, or has an unknown unit. The manifest records SHA256 hashes of both the archived config and raw output.

**CModel rule:** physical TSV delay/energy comes from DESTINY/CACTI-3DD. Aggregate lane count, serialization, finite FIFO depth, credits, and WBUF backpressure remain architectural parameters.

## CACTI reference lineage

- Repository: `HewlettPackard/cacti`
- Pinned public revision: `1ffd8dfb10303d306ecd8d215320aea07651e878`
- Relevant source: `TSV.cc`

CACTI is source-lineage validation for DESTINY's CACTI-3DD-derived TSV model. The CModel does not add an independent CACTI TSV energy number on top of DESTINY.

## Ramulator2.1 / HBM3 reference backend

- Repository: `CMU-SAFARI/ramulator2`
- Pinned revision: `b30320bc9385b708e86b67ebb9f48858cc66d798`
- Python package version: `2.1.0`
- Default HBM3 components:
  - device: `HBM3`
  - organization: `HBM3_8Gb_8hi`
  - timing: `HBM3_6400Mbps`
  - controller: `HBM34`
  - scheduler: `FRFCFSRowHit`
  - row policy: `Open`
  - address mapper: `RoBaRaCoCh`
  - refresh manager: `HBM34PerBankRefresh`
  - frontend: `LoadStoreTrace`

The HBM backend consumes the same ordered/repeated physical transaction stream produced by the CModel mapper. Every run archives the trace and generated runner hashes, selected components, returned `sim.stats`, package version, and native `_ramulator` extension SHA256.

Example:

```bash
python -m rram_cmodel.cli hbm-trace \
  --M 1 --K 64 --N 128 --weight-bits 8 \
  --workdir results/ramulator21_smoke
```

## End-to-end validation

The same pipeline used by the external-backend GitHub Action is available locally:

```bash
bash scripts/run_full_validation.sh
```

It performs pinned checkout/build, unit tests, a real DESTINY run + manifest verification, a calibrated CModel smoke comparison, a real Ramulator2.1 HBM3 trace, and a small DSE sweep.

## ATTACC methodological reference

ATTACC is a methodological reference rather than a runtime dependency. Its Python system model generates PIM traffic and delegates detailed memory timing to its Ramulator2 fork. This repository follows the same separation of concerns while keeping RRAM/TSV/FIFO/WBUF/PE event timing explicit in the NMP CModel.
