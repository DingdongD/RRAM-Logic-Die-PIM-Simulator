# External modeling backends

This directory documents the external tools used to calibrate or validate the CModel. They are **not vendored** into this repository; publication runs should check out the exact revisions below and archive the generated manifests/traces.

## DESTINY / ReRAM + TSV physical calibration

- Repository: `sparsh0mittal/destiny_3d_cache`
- Inspected revision: `32ef839f9f32484a7457f8013b2a0883e757300b`
- Relevant sources:
  - `TSV.h`
  - `TSV.cpp`
  - `Result.cpp`
  - `macros.h`

The DESTINY TSV implementation states that it contains code from CACTI-3DD. It obtains TSV parasitic capacitance/resistance/area from technology parameters, optionally sizes a buffer chain with logical effort, computes delay with the Horowitz model, and computes dynamic energy from capacitive switching. `Result.cpp` separately reports stacked TSV latency and dynamic energy.

**CModel rule:** DESTINY/CACTI-3DD supplies physical TSV delay/energy/area. Architectural lane count, serializer width, FIFO depth, and credits are separate architecture parameters and must not be inferred from the physical RC model.

## CACTI reference lineage

- Repository: `HewlettPackard/cacti`
- Pinned public revision: `1ffd8dfb10303d306ecd8d215320aea07651e878`
- Relevant source: `TSV.cc`

CACTI's TSV model is used only as source-lineage validation for the DESTINY TSV implementation in this CModel. The simulator does not independently combine CACTI and DESTINY TSV energy numbers.

## Ramulator2.1 / HBM3 reference backend

- Repository: `CMU-SAFARI/ramulator2`
- Inspected revision: `b30320bc9385b708e86b67ebb9f48858cc66d798`
- Python package version: `2.1.0`
- Default HBM3 components used by `Ramulator21HBM3Adapter`:
  - device: `HBM3`
  - organization: `HBM3_8Gb_8hi`
  - timing: `HBM3_6400Mbps`
  - controller: `HBM34`
  - scheduler: `FRFCFSRowHit`
  - row policy: `Open`
  - address mapper: `RoBaRaCoCh`
  - refresh manager: `HBM34PerBankRefresh`
  - trace frontend: `LoadStoreTrace`

Every trace run records the requested source revision, package version, generated runner SHA256, trace SHA256, native extension path/SHA256, component configuration, and returned statistics. The adapter rejects a different installed package version unless the experiment configuration is explicitly changed.

## ATTACC methodological reference

- Repository: `scale-snu/attacc_simulator`

ATTACC is a methodological reference rather than a runtime dependency. Its Python system model generates PIM traces and delegates detailed memory timing to its Ramulator2 fork. This repository follows the same separation of concerns while keeping RRAM/TSV/WBUF event timing explicit in the NMP CModel.
