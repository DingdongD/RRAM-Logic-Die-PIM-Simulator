from dataclasses import dataclass


@dataclass(frozen=True)
class GemmWorkload:
    """Y[M,N] = A[M,K] @ W[K,N], with W stored row-major."""

    M: int
    K: int
    N: int
    weight_bits: int = 8
    activation_bits: int = 8
    psum_bits: int = 32

    def __post_init__(self) -> None:
        for name in ("M", "K", "N", "weight_bits", "activation_bits", "psum_bits"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")

    @property
    def macs(self) -> int:
        return self.M * self.K * self.N

    @property
    def weight_storage_bits(self) -> int:
        return self.K * self.N * self.weight_bits
