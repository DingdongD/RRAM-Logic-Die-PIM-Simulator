from dataclasses import replace

from rram_cmodel.presets import functional_example
from rram_cmodel.simulator import ArchitectureKind, CycleEventSimulator
from rram_cmodel.workload import GemmWorkload


def test_compare_preserves_identical_mac_count():
    cfg = functional_example()
    w = GemmWorkload(M=1, K=256, N=256, weight_bits=8)
    results = [CycleEventSimulator(cfg).run(w, a) for a in ArchitectureKind]
    assert all(x.macs == w.macs for x in results)


def test_wider_tsv_cannot_make_nmp_slower():
    base = functional_example()
    w = GemmWorkload(M=1, K=1024, N=128, weight_bits=8)
    slow = CycleEventSimulator(replace(base, tsv=replace(base.tsv, width_bits_per_cycle=128))).run(w, ArchitectureKind.RRAM_NMP)
    fast = CycleEventSimulator(replace(base, tsv=replace(base.tsv, width_bits_per_cycle=1024))).run(w, ArchitectureKind.RRAM_NMP)
    assert fast.cycles <= slow.cycles


def test_more_rram_banks_cannot_make_nmp_slower_for_stream():
    base = functional_example()
    w = GemmWorkload(M=1, K=1024, N=128, weight_bits=8)
    few = CycleEventSimulator(replace(base, rram=replace(base.rram, num_banks=4))).run(w, ArchitectureKind.RRAM_NMP)
    many = CycleEventSimulator(replace(base, rram=replace(base.rram, num_banks=64))).run(w, ArchitectureKind.RRAM_NMP)
    assert many.cycles <= few.cycles


def test_wbuf_read_bandwidth_limits_compute_throughput():
    base = functional_example()
    w = GemmWorkload(M=1, K=128, N=128, weight_bits=8)
    full = CycleEventSimulator(base).run(w, ArchitectureKind.RRAM_NMP)
    narrow_cfg = replace(base, wbuf=replace(base.wbuf, read_bits_per_cycle=512))
    narrow = CycleEventSimulator(narrow_cfg).run(w, ArchitectureKind.RRAM_NMP)
    assert narrow.cycles > full.cycles
