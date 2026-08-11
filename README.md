# RRAM Logic-Die PIM Simulator

Research-oriented Python CModel for a **persistent RRAM weight store + TSV + ping-pong WBUF + logic-die PE line**, with matched Pure-NPU baselines.

The modeled architecture is near-memory processing (NMP), not analog RRAM-CIM: RRAM stores weights while MACs execute on the logic die. The key comparison is therefore the weight-delivery path:

- **RRAM-NMP:** `RRAM banks -> physical TSV -> finite RX FIFO -> banked WBUF -> 128-PE line`
- **NPU-direct:** `HBM -> NoC/DMA -> banked WBUF -> 128-PE line`
- **NPU-hierarchical:** `HBM -> NoC -> GBUF -> NoC -> banked WBUF -> 128-PE line`

The PE line, WBUF organization, precision, dataflow, and workload mapping are shared across all modes so that differences are attributable to the weight hierarchy.

## Modeling philosophy

The primary backend is transaction/event level rather than a single roofline `L + D/BW` equation. It explicitly models exact row-major GEMM weight addresses, strided tiles, physical read granules and overfetch, bank mapping and per-bank issue constraints, calibrated TSV physical propagation/energy, finite TSV FIFO credits/backpressure, WBUF bank/port conflicts, ping-pong overlap, PE stalls/utilization, and access/bit-based energy accounting.

A closed-form analytical model is retained only as a fast cross-check. Publication runs should calibrate RRAM/TSV timing and energy from a pinned DESTINY run and validate the Pure-NPU HBM source with pinned Ramulator2.1. Functional defaults in the repository are deliberately marked as **not silicon calibrated**.

## Architecture contract

For `Y[M,N] = A[M,K] @ W[K,N]`, the PE line unfolds `N` across up to 128 lanes. In one `(m,k)` step, one activation is broadcast and lane `j` consumes `W[k,n+j]`.

V0.2 still requires `M <= psum_contexts`. This prevents the simulator from silently inventing a PSUM spill/refetch policy. Explicit PSUM spill support is a planned extension.

## Quick start

```bash
python -m pip install -e .
python -m rram_cmodel.cli compare --M 1 --K 4096 --N 4096 --weight-bits 8
```

DSE example:

```bash
python -m rram_cmodel.cli dse \
  --M 1 --K 4096 --N 4096 --weight-bits 8 \
  --rram-banks 8 16 32 64 128 \
  --tsv-width 128 256 512 1024 \
  --tsv-fifo-depth 2 4 8 16 \
  --wbuf-banks 8 16 32 \
  --wbuf-kb 8 16 32 64 128 \
  --output results/dse.csv
```

## Reproducible external-backend validation

Pinned DESTINY and Ramulator2.1 sources are bootstrapped under `third_party/` from revisions recorded in `third_party/versions.json`. The canonical pipeline is:

```bash
bash scripts/run_full_validation.sh
```

The pipeline performs pinned checkout/build, simulator tests, a **real stacked-ReRAM DESTINY run**, manifest normalization and SHA256 verification, a calibrated NPU-vs-NMP comparison, a **real ordered HBM3 trace through Ramulator2.1**, and a small DSE. The same pipeline is exercised by `.github/workflows/external-backends.yml` and has passed end-to-end in GitHub Actions.

The checked-in 180 nm / 1 MiB / 256-bit / 4-stack DESTINY configuration exists only as a reproducible **reference calibration macro**. It proves the pipeline and normalization path; paper-quality silicon claims should substitute an experiment-specific calibrated device/technology configuration. The validated reference Ramulator smoke test issued 256 ordered 32-byte HBM3 transactions and completed in 1101 controller cycles.

For the full command-by-command local procedure and host requirements, see `docs/LOCAL_PIPELINE.md`.

## Output metrics

Each architecture reports total cycles/time, useful MACs, PE utilization, weight-wait stall cycles, useful/transferred weight bits, physical overfetch ratio, per-stage traffic, TSV queue stall/max occupancy, per-component dynamic energy, static energy, and total energy. Comparison mode additionally reports speedup and energy ratios versus both Pure-NPU baselines.

## Calibration rule

`rram_cmodel.presets.functional_example()` is only for simulator bring-up and unit tests. Paper-quality experiments should record exact parameter provenance:

- RRAM macro latency/cycle time/energy/leakage: pinned DESTINY calibration manifest.
- TSV physical delay/energy: the same DESTINY/CACTI-3DD-lineage run; lane count/FIFO/credits remain architecture knobs.
- HBM timing: pinned Ramulator2.1 with archived exact trace, component config, `sim.stats`, package version and native-extension SHA256.
- WBUF/GBUF SRAM energy: CACTI/Accelergy or another cited source.
- MAC/ACC energy: synthesized RTL or a technology-normalized cited source.

The simulator never silently substitutes functional defaults in a manifest-calibrated run.

## Validation invariants

Tests cover independent bank issue, per-bank issue intervals, physical granule overfetch, identical useful MAC counts across architectures, monotonic TSV/bank scaling, finite FIFO backpressure, WBUF read/write bank/port throttling, ordered/repeated Ramulator2 transaction generation, W4 packed-address traces, DESTINY read-vs-write subtree disambiguation, immutable manifest hashes, explicit TSV hop scaling, and rejection of implicit PSUM spilling/repacketization.

## Current model boundary

The RRAM path is event-coupled through RRAM banks, TSV, FIFO, WBUF and PE consumption. The official Ramulator2.1 HBM3 run currently serves as a source-level reference returning aggregate controller statistics. The simulator deliberately does **not** fabricate per-request HBM completion timestamps from aggregate cycles. Full Ramulator request-completion coupling requires a frontend/backend extension that exposes completion events.
