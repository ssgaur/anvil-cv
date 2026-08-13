"""Model provider abstraction.

Anvil originally spoke only SAM2, and `engine.py` assumed a single global
predictor with point/box prompts returning exactly one mask. SAM3 breaks both
assumptions: it takes a *text concept* and returns *every* matching instance.

Rather than sprinkling `if model == "sam3"` through the service, each model is
wrapped in a provider that declares what it can actually do. Callers ask for a
capability instead of hardcoding a model name.

Providers are loaded lazily and can be released, because on a 16GB laptop SAM2
(~2.1GB) and SAM3 (~3.5GB) together leave little headroom.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Capabilities:
    """What a model can be prompted with, and what it returns.

    The frontend reads this so it can hide prompts a model does not support
    instead of failing at request time.
    """

    point: bool = False
    box: bool = False
    text: bool = False
    mask: bool = False
    # True when one call can return many instances (SAM3 concept segmentation).
    multi_instance: bool = False
    video: bool = False
    # True when the encoder output can be cached to disk and replayed (SAM2).
    embedding_cache: bool = False
    # Rough resident cost, used for admission control before loading.
    approx_memory_gb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Instance:
    """One detected object. SAM2 returns a single one; SAM3 returns many."""

    polygon: list[list[float]] = field(default_factory=list)
    bbox: list[float] = field(default_factory=list)
    score: float = 0.0
    # Compressed COCO RLE, preserved so mask fidelity survives polygon
    # simplification. None when the provider only produces polygons.
    mask_rle: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "polygon": self.polygon,
            "bbox": self.bbox,
            "score": self.score,
        }
        if self.mask_rle is not None:
            out["mask_rle"] = self.mask_rle
        return out


class CapabilityError(RuntimeError):
    """Raised when a provider is asked for something it does not support."""


@runtime_checkable
class ModelProvider(Protocol):
    """Common surface for every segmentation model.

    Deliberately narrow: only the operations Anvil actually calls. Providers
    raise `CapabilityError` for anything they do not implement, so unsupported
    combinations fail loudly rather than silently returning nothing.
    """

    name: str

    def capabilities(self) -> Capabilities: ...

    def is_loaded(self) -> bool: ...

    def load(self) -> None: ...

    def release(self) -> None: ...

    def predict(
        self,
        image_path: str,
        embedding_path: str | None = None,
        points: list[list[float]] | None = None,
        point_labels: list[int] | None = None,
        box: list[float] | None = None,
        adapter_path: str | None = None,
    ) -> Instance: ...

    def predict_concept(
        self,
        image_path: str,
        text: str,
        threshold: float = 0.5,
        max_instances: int = 100,
    ) -> list[Instance]: ...
