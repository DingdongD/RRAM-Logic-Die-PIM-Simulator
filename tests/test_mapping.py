import pytest

from rram_cmodel.mapping import GemmMapper
from rram_cmodel.presets import functional_example
from rram_cmodel.workload import GemmWorkload


def test_weight_segments_are_strided_by_full_N():
    cfg = functional_example()
    w = GemmWorkload(M=1, K=2, N=256, weight_bits=8)
    tile = GemmMapper(w, cfg.pe, cfg.wbuf).tiles()[0]
    segs = tile.weight_segments(w)
    assert len(segs) == 2
    assert segs[0].address_bit == 0
    assert segs[1].address_bit == 256 * 8
    assert segs[0].size_bits == 128 * 8


def test_mapper_rejects_implicit_psum_spill():
    cfg = functional_example()
    w = GemmWorkload(M=cfg.pe.psum_contexts + 1, K=64, N=128)
    with pytest.raises(ValueError):
        GemmMapper(w, cfg.pe, cfg.wbuf)
