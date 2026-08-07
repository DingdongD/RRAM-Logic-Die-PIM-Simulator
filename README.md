# RRAM Logic-Die PIM Simulator

Research-oriented Python CModel for a **persistent RRAM weight store + TSV + ping-pong WBUF + logic-die PE line**, with matched Pure-NPU baselines.

The modeled architecture is near-memory processing (NMP), not analog RRAM-CIM: RRAM stores weights while MACs execute on the logic die. The key comparison is therefore the weight-delivery path:

- **RRAM-NMP:** `RRAM banks -> TSV -> WBUF -> 128-PE line`
- **NPU-direct:** `HBM -> NoC/DMA -> WBUF -> 128-PE line`
- **NPU-hierarchical:** `HBM -> NoC -> GBUF -> NoC -> WBUF -> 128-PE line`

The PE line, WBUF read side, precision, dataflow, and workload mapping are shared across all modes so that differences are attributable to the weight hierarchy.

## Modeling philosophy

The primary backend is transaction/event level rather than a single roofline `L + D/BW` equation. It explicitly models exact row-major GEMM weight addresses, strided tiles, physical read granules and overfetch, bank mapping and per-bank issue constraints, packetized TSV/NoC/SRAM-port traversal, ping-pong WBUF overlap, WBUF read bandwidth, PE stalls/utilization, and access/bit-based energy accounting.

A closed-form analytical model is retained only as a fast cross-check. Publication runs should calibrate RRAM timing/energy from DESTINY (or another cited source) and Pure-NPU HBM timing from Ramulator2. Functional defaults in the repository are deliberately marked as **not silicon calibrated**.

## Architecture contract

For `Y[M,N] = A[M,K] @ W[K,N]`, the PE line unfolds `N` across up to 128 lanes. In one `(m,k)` step, one activation is broadcast and lane `j` consumes `W[k,n+j]`.

V0.1 requires `M <= psum_contexts`. This prevents the simulator from silently inventing a PSUM spill/refetch policy. Explicit PSUM spill support is a planned extension.

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
  --wbuf-kb 8 16 32 64 128 \
  --output results/dse.csv
```

Run validation:

```bash
pytest -q
```

## Output metrics

Each architecture reports total cycles/time, useful MACs, PE utilization, weight-wait stall cycles, useful/transferred weight bits, physical overfetch ratio, per-stage traffic, per-component dynamic energy, static energy, and total energy. Comparison mode additionally reports speedup and energy ratios versus both Pure-NPU baselines.

## Calibration rule

`rram_cmodel.presets.functional_example()` is only for simulator bring-up and unit tests. Paper-quality experiments should record exact parameter provenance:

- RRAM macro latency/energy/area: DESTINY or an explicitly cited device/macro model.
- TSV width/latency/energy: selected 3D integration model / cited source.
- HBM timing: Ramulator2 with archived YAML and exact version/commit.
- WBUF/GBUF SRAM energy: CACTI/Accelergy or another cited source.
- MAC/ACC energy: synthesized RTL or a technology-normalized cited source.

The simulator should never silently substitute functional defaults in reported experiments.

## Validation invariants

Tests cover independent bank issue, per-bank issue intervals, physical granule overfetch, identical useful MAC counts across architectures, monotonic TSV/bank scaling, WBUF read-bandwidth throttling, exact Ramulator2 transaction-address generation, and rejection of implicit PSUM spilling.

## Planned strictness upgrades

1. Explicit RRAM die/rank/bank hierarchy and controller queue policy.
2. Finite TSV FIFO/credits and backpressure.
3. WBUF bank conflicts and configurable read/write port topology.
4. Activation-buffer and output/PSUM traffic timing.
5. Explicit PSUM spill/refetch policy.
6. Multiple 128-PE lines sharing RRAM/TSV resources.
7. Full Ramulator2 request-trace backend for Pure NPU experiments.
8. Automated DESTINY sweeps and immutable calibration manifests.
9. Cross-validation against selected SCALE-Sim/Timeloop mappings.
