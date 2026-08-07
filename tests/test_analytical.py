from rram_cmodel.analytical import FastAnalyticalSimulator
from rram_cmodel.presets import functional_example
from rram_cmodel.simulator import ArchitectureKind
from rram_cmodel.workload import GemmWorkload


def test_fast_model_is_nonzero_and_same_architecture_contract():
    cfg = functional_example()
    w = GemmWorkload(M=1, K=256, N=128, weight_bits=8)
    r = FastAnalyticalSimulator(cfg).run(w, ArchitectureKind.RRAM_NMP)
    assert r.cycles > 0
    assert 0 < r.pe_utilization <= 1
