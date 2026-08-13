"""SAM2 provider — a thin wrapper over the existing engine.

Intentionally delegates to `anvil_ml.engine` rather than reimplementing
anything. SAM2 behaviour, the embedding cache and the LoRA adapter path must
stay bit-for-bit identical; this only reshapes the return value into the
common `Instance` type.
"""

from __future__ import annotations

from anvil_ml.providers.base import Capabilities, CapabilityError, Instance

_CAPS = Capabilities(
    point=True,
    box=True,
    text=False,          # SAM2 has no text encoder
    mask=False,
    multi_instance=False,  # one prompt -> one best mask
    video=False,           # not wired up in Anvil
    embedding_cache=True,
    approx_memory_gb=2.1,  # measured: base_plus peak during embed
)


class SAM2Provider:
    name = "sam2"

    def capabilities(self) -> Capabilities:
        return _CAPS

    def is_loaded(self) -> bool:
        from anvil_ml import engine

        return engine._predictor is not None

    def load(self) -> None:
        from anvil_ml import engine

        engine.get_predictor()

    def release(self) -> None:
        """Drop the cached predictor to reclaim ~2GB.

        Also clears adapter predictors, which hold their own model copies.
        """
        import gc

        from anvil_ml import engine

        with engine._lock:
            engine._predictor = None
            engine._model = None
        try:
            from anvil_ml import training

            training._adapter_predictors.clear()
        except (ImportError, AttributeError):
            pass
        gc.collect()

    def predict(
        self,
        image_path: str,
        embedding_path: str | None = None,
        points: list[list[float]] | None = None,
        point_labels: list[int] | None = None,
        box: list[float] | None = None,
        adapter_path: str | None = None,
    ) -> Instance:
        from anvil_ml import engine

        raw = engine.predict(
            image_path=image_path,
            embedding_path=embedding_path,
            points=points,
            point_labels=point_labels,
            box=box,
            adapter_path=adapter_path,
        )
        return Instance(
            polygon=raw.get("polygon", []),
            bbox=raw.get("bbox", [0, 0, 0, 0]),
            score=float(raw.get("score", 0.0)),
        )

    def predict_concept(
        self,
        image_path: str,
        text: str,
        threshold: float = 0.5,
        max_instances: int = 100,
    ) -> list[Instance]:
        raise CapabilityError(
            "sam2 does not support text prompts; use point/box prompts or select SAM3"
        )
