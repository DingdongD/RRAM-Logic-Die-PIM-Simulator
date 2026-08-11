from __future__ import annotations

from .config import (
    BankedMemoryConfig,
    ClockConfig,
    PELineConfig,
    StreamStageConfig,
    SystemConfig,
    WBufferConfig,
)


def functional_example() -> SystemConfig:
    """Non-silicon-calibrated preset for unit tests and software bring-up.

    Replace RRAM numbers with DESTINY output and HBM timing with Ramulator2
    before publication. These values must not be presented as device results.
    """
    wbuf_write = StreamStageConfig(
        name="wbuf_write",
        width_bits_per_cycle=1024,
        latency_cycles=1,
        energy_pj_per_bit=0.01,
    )
    return SystemConfig(
        clock=ClockConfig(freq_hz=1e9),
        pe=PELineConfig(
            num_pe=128,
            psum_contexts=8,
            mac_energy_pj=0.25,
            accumulator_energy_pj=0.05,
            startup_cycles_per_tile=1,
        ),
        wbuf=WBufferConfig(
            capacity_bytes=32 * 1024,
            read_bits_per_cycle=1024,
            write_stage=wbuf_write,
            read_energy_pj_per_bit=0.005,
        ),
        rram=BankedMemoryConfig(
            name="rram",
            num_banks=32,
            capacity_bytes=64 * 1024 * 1024,
            read_granule_bits=256,
            read_latency_cycles=8,
            issue_interval_cycles=4,
            read_energy_pj_per_access=5.0,
        ),
        tsv=StreamStageConfig(
            name="tsv",
            width_bits_per_cycle=1024,
            latency_cycles=2,
            energy_pj_per_bit=0.02,
        ),
        hbm=BankedMemoryConfig(
            name="hbm",
            num_banks=16,
            capacity_bytes=64 * 1024 * 1024,
            read_granule_bits=256,
            read_latency_cycles=60,
            issue_interval_cycles=8,
            read_energy_pj_per_access=50.0,
        ),
        npu_direct_stages=[
            StreamStageConfig(
                name="noc_direct",
                width_bits_per_cycle=1024,
                latency_cycles=4,
                energy_pj_per_bit=0.05,
            )
        ],
        npu_hierarchical_stages=[
            StreamStageConfig(
                name="noc_hbm_to_gbuf",
                width_bits_per_cycle=1024,
                latency_cycles=4,
                energy_pj_per_bit=0.05,
            ),
            StreamStageConfig(
                name="gbuf_write",
                width_bits_per_cycle=1024,
                latency_cycles=1,
                energy_pj_per_bit=0.02,
            ),
            StreamStageConfig(
                name="gbuf_read",
                width_bits_per_cycle=1024,
                latency_cycles=1,
                energy_pj_per_bit=0.02,
            ),
            StreamStageConfig(
                name="noc_gbuf_to_wbuf",
                width_bits_per_cycle=1024,
                latency_cycles=4,
                energy_pj_per_bit=0.05,
            ),
        ],
        metadata={"calibration": "functional_only_not_for_publication"},
    )
