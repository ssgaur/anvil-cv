"""SAM3 provider via HuggingFace Transformers.

Uses `transformers` rather than the `facebookresearch/sam3` repo for inference
because the native repo hardcodes CUDA in several paths and ships fused kernels
that do not run on Apple Silicon. The Transformers path is plain PyTorch, so the
same code runs CPU-only on an M1 laptop and on an Azure GPU without changes.
The native checkpoint (`sam3.pt`) is still needed for *training*.

Measured on M1 (16GB, CPU): 3.13GB weights, 3.52GB peak RSS, ~13s per query.
Do NOT switch to bfloat16 on CPU — it halves memory but was 11x slower
(149.8s vs 13.4s) because ARM CPUs lack fast bf16 kernels here.
"""

from __future__ import annotations

import logging
import os
import threading

import numpy as np

from anvil_ml.providers.base import Capabilities, CapabilityError, Instance

log = logging.getLogger("anvil.ml.sam3")

_CAPS = Capabilities(
    point=False,   # interactive refinement stays with SAM2 for now
    box=False,
    text=True,
    mask=False,
    multi_instance=True,
    video=False,   # SAM3 video inference exists upstream but is not wired up
    embedding_cache=False,  # no reusable per-image encoder cache in this path
    approx_memory_gb=3.5,   # measured peak RSS, fp32
)

DEFAULT_MODEL_ID = "facebook/sam3"


class SAM3Provider:
    name = "sam3"

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._lock = threading.Lock()

    def capabilities(self) -> Capabilities:
        return _CAPS

    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        import time

        import torch

        with self._lock:
            if self._model is not None:
                return
            from transformers import Sam3Model, Sam3Processor

            model_id = os.getenv("SAM3_MODEL_ID", DEFAULT_MODEL_ID)
            t0 = time.time()
            self._processor = Sam3Processor.from_pretrained(model_id)
            # fp32 deliberately: see module docstring.
            self._model = Sam3Model.from_pretrained(model_id, dtype=torch.float32)
            self._model.eval()
            log.info(
                "sam3_loaded",
                extra={"model_id": model_id, "load_s": round(time.time() - t0, 2)},
            )

    def release(self) -> None:
        import gc

        with self._lock:
            self._model = None
            self._processor = None
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
        raise CapabilityError(
            "sam3 in Anvil currently supports text concept prompts only; "
            "use SAM2 for point/box refinement"
        )

    def predict_concept(
        self,
        image_path: str,
        text: str,
        threshold: float = 0.5,
        max_instances: int = 100,
    ) -> list[Instance]:
        """Segment every instance matching `text`.

        Returns an empty list when the concept is genuinely absent — the caller
        must decide whether that means "verified absent" or "not annotated yet".
        """
        import time

        import torch
        from PIL import Image as PILImage

        if not text or not text.strip():
            raise ValueError("text concept must be a non-empty string")

        self.load()
        img = PILImage.open(image_path).convert("RGB")

        t0 = time.time()
        inputs = self._processor(images=img, text=text.strip(), return_tensors="pt")
        with torch.no_grad():
            outputs = self._model(**inputs)
        results = self._processor.post_process_instance_segmentation(
            outputs,
            threshold=threshold,
            mask_threshold=0.5,
            target_sizes=inputs.get("original_sizes").tolist(),
        )[0]

        instances: list[Instance] = []
        masks = results.get("masks")
        boxes = results.get("boxes")
        scores = results.get("scores")
        for i in range(min(len(scores), max_instances)):
            mask = masks[i].cpu().numpy().astype(np.uint8)
            polygon = _mask_to_polygon(mask)
            if not polygon:
                continue
            instances.append(
                Instance(
                    polygon=polygon,
                    bbox=_xyxy_to_xywh([float(v) for v in boxes[i]]),
                    score=float(scores[i]),
                    mask_rle=_encode_rle(mask),
                )
            )

        log.info(
            "sam3_predict_concept",
            extra={
                "text": text,
                "threshold": threshold,
                "n_instances": len(instances),
                "duration_ms": round((time.time() - t0) * 1000, 2),
            },
        )
        return instances


def _mask_to_polygon(mask: np.ndarray) -> list[list[float]]:
    """Largest external contour as a simplified polygon.

    Matches SAM2's representation so both models feed the same annotation
    editor. The exact mask is preserved separately as RLE.
    """
    import cv2

    contours, _ = cv2.findContours(
        (mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    if not contours:
        return []
    cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(cnt) <= 0:
        return []
    epsilon = 0.0015 * cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, epsilon, True)
    return [[float(p[0][0]), float(p[0][1])] for p in approx]


def _xyxy_to_xywh(box: list[float]) -> list[float]:
    x1, y1, x2, y2 = box[:4]
    return [x1, y1, x2 - x1, y2 - y1]


def _encode_rle(mask: np.ndarray) -> dict | None:
    """Compressed COCO RLE, so SAM3 mask fidelity is not lost to polygons."""
    try:
        from pycocotools import mask as mask_util
    except ImportError:
        return None
    rle = mask_util.encode(np.asfortranarray((mask > 0).astype(np.uint8)))
    counts = rle.get("counts")
    if isinstance(counts, bytes):
        rle["counts"] = counts.decode("ascii")
    return dict(rle)
