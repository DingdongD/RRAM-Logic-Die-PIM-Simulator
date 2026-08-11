# RRAM Logic-Die PIM Simulator

Research-oriented Python CModel for a **persistent RRAM weight store + TSV + ping-pong WBUF + logic-die PE line**, with matched Pure-NPU baselines.

The modeled architecture is near-memory processing (NMP), not analog RRAM-CIM: RRAM stores weights while MACs execute on the logic die. The key comparison is therefore the weight-delivery path:

- **RRAM-NMP:** `RRAM banks -> physical TSV -> finite RX FIFO -> banked WBUF -> 128-PE line`
- **NPU-direct:** `HBM -> NoC/DMA -> same banked WBUF -> same 128-PE line`
- **NPU-hierarchical:** `HBM -> NoC -> GBUF -> NoC -> same banked WBUF -> same 128-PE line`

The PE line, WBUF organization, precision, dataflow, and workload mapping are shared across all modes so that differences remain attributable to the weight hierarchy.

## Modeling philosophy

The primary backend is transaction/event level rather than a single roofline `L + D/BW` equation. It explicitly models exact row-major GEMM weight addresses, strided tiles, physical read granules and overfetch, bank mapping and per-bank issue constraints, TSV serialization, finite receive credits/backpressure, WBUF bank/port service, ping-pong overlap, WBUF read-bank supply, PE stalls/utilization, and access/bit-based energy accounting.

A closed-form analytical model is retained only as a fast cross-check. Functional defaults in `rram_cmodel.presets.functional_example()` are deliberately **not silicon calibrated** and must not be reported as device results.

## Physical model vs. architecture model

The simulator intentionally separates physical calibration from architectural scheduling.

### Physical calibration

- **RRAM macro:** latency, read cycle time, access energy, and static power come from an explicit DESTINY calibration manifest or another documented backend.
- **TSV lane:** propagation delay, dynamic energy per transferred data bit, and occupied area per lane come from the DESTINY TSV model, whose implementation is derived from CACTI-3DD. The referenced open-source model uses TSV parasitic R/C, optional driver-chain sizing, Horowitz delay, and capacitive dynamic energy.
- **HBM:** detailed DRAM timing is delegated to Ramulator2 rather than duplicated in Python.
- **SRAM/MAC energy:** WBUF/GBUF and MAC/ACC values should eventually be calibrated from CACTI/Accelergy/synthesis or another documented source.

### Architecture knobs

- RRAM bank count and weight placement.
- Number of simultaneously driven data TSV lanes (`tsv.width_bits_per_cycle`).
- TSV receive FIFO packet size/depth and credit policy.
- WBUF bank count, bank word size, read/write ports, and aggregate datapath width.
- PE count and PSUM contexts.

A physical TSV RC model does **not** determine FIFO depth or architectural TSV lane count. Conversely, FIFO/backpressure parameters are never presented as device-level TSV properties.

## Architecture contract

For `Y[M,N] = A[M,K] @ W[K,N]`, the PE line unfolds `N` across up to 128 lanes. In one `(m,k)` step, one activation is broadcast and lane `j` consumes `W[k,n+j]`.

The current mapper requires `M <= psum_contexts`. This prevents the simulator from silently inventing a PSUM spill/refetch policy. The current TSV path also requires one physical RRAM read response to fit in one configured FIFO packet; wider responses require an explicit repacketizer model rather than silent fragmentation.

## V0.2 timing path

The RRAM path is a coupled event model:

```text
RRAM bank request
    |
    v
RRAM response ready
    |
    v
TSV serializer + calibrated physical delay
    |
    v
finite TSV RX FIFO / credits
    |
    v
WBUF bank-word writes + per-bank write ports
    |
    v
ping-pong WBUF half ready
    |
    v
WBUF read banks/ports -> 128 PE lanes
```

When the RX FIFO is full, the TSV serializer is held until a credit becomes available. With source backpressure enabled, this blocking is propagated to the issuing RRAM bank. A FIFO credit is conservatively released only after the whole packet has completed its WBUF bank-word writes.

## Quick start

```bash
python -m pip install -e .
python -m rram_cmodel.cli compare --M 1 --K 4096 --N 4096 --weight-bits 8
```

Calibrated RRAM/TSV run:

```bash
python -m rram_cmodel.cli compare \
  --M 1 --K 4096 --N 4096 --weight-bits 8 \
  --destiny-manifest calibration/destiny_rram.json
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

## Ramulator2 HBM3 trace mode

The exact ordered weight-address stream can be replayed through **official Ramulator2.1** HBM3:

```bash
python -m rram_cmodel.cli hbm-trace \
  --M 1 --K 4096 --N 4096 --weight-bits 8 \
  --workdir results/ramulator21
```

The default component choices are explicit in `Ramulator21HBM3Config` (`HBM3_8Gb_8hi`, `HBM3_6400Mbps`, `HBM34`, `FRFCFSRowHit`, `RoBaRaCoCh`, HBM34 per-bank refresh). Every run records the exact trace, runner, hashes, component configuration, and returned `sim.stats`.

The repository also retains an ATTACC-compatible binary/YAML adapter for Ramulator2 forks that use `ramulator2 -f <yaml>`.

**Important:** current `hbm-trace` is a source-level HBM reference/validation mode. It does not fabricate per-request HBM completion timestamps from aggregate controller cycles. The event-fast baseline remains the per-tile DSE model until a Ramulator frontend exposing request completion timestamps is integrated.

## DESTINY calibration manifest

`DestinyCalibrationManifest` separates normalized RRAM-bank values from per-data-TSV physical values and records:

- DESTINY repository/commit;
- exact DESTINY config path + SHA256;
- exact raw output path + SHA256;
- normalized RRAM organization/timing/energy;
- TSV model lineage/type/buffering, delay, energy/bit, area/lane, and optional R/C;
- notes required to explain extraction/normalization.

The loader fails on missing provenance, modified source files, unsupported schema versions, or TSV lineage that does not explicitly identify the CACTI-3DD-derived DESTINY model. Units are never inferred from arbitrary output text.

## Output metrics

Each architecture reports total cycles/time, useful MACs, PE utilization, weight-wait stall cycles, useful/transferred weight bits, physical overfetch ratio, per-stage traffic, queue-stall cycles, maximum FIFO occupancy, per-component dynamic energy, static energy, and total energy. Comparison mode additionally reports speedup and energy ratios versus both Pure-NPU baselines.

## Validation invariants

Tests cover independent bank issue, per-bank issue intervals, physical granule overfetch, identical useful MAC counts across architectures, monotonic TSV/bank scaling, finite-FIFO monotonicity, WBUF read/write bank/port throttling, explicit rejection of hidden TSV repacketization, ordered/repeated Ramulator transactions, packed-W4 traces, Ramulator2.1 runner generation, and DESTINY manifest integrity/application.

Run validation:

```bash
pytest -q
```

## Remaining strictness upgrades

1. Expose per-request HBM completion timestamps from a Ramulator2 frontend and couple them directly to downstream NoC/WBUF events.
2. Add explicit RRAM die/rank/channel controller queues and selectable bank-address policies.
3. Add activation-buffer and output/PSUM traffic timing.
4. Add explicit PSUM spill/refetch mapping.
5. Extend to multiple 128-PE lines sharing RRAM/TSV resources.
6. Add CACTI/Accelergy-backed WBUF/GBUF calibration manifests.
7. Cross-validate selected mappings against SCALE-Sim/Timeloop.
