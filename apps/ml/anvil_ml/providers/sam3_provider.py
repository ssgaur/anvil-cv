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
    point=True,
    box=True,
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
            try:
                self._processor = Sam3Processor.from_pretrained(model_id)
                # fp32 deliberately: see module docstring.
                self._model = Sam3Model.from_pretrained(model_id, dtype=torch.float32)
            except Exception as e:
                raise RuntimeError(
                    f"Unable to load SAM3 weights ({model_id}). "
                    "facebook/sam3 is a gated Hugging Face repository: accept the license at "
                    "https://huggingface.co/facebook/sam3, authenticate on the host with "
                    "`hf auth login` (or set HF_TOKEN in .env), and run `make sam3-setup`."
                ) from e
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
        import time

        import torch
        from PIL import Image as PILImage

        if not points and not box:
            raise CapabilityError("either points or box is required for SAM3 predict")

        self.load()
        img = PILImage.open(image_path).convert("RGB")
        w, h = img.size

        prompt_boxes: list[list[float]] = []
        prompt_labels: list[int] = []

        if box and len(box) >= 4:
            x1, y1, x2, y2 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
            prompt_boxes.append([
                max(0.0, min(x1, x2)),
                max(0.0, min(y1, y2)),
                min(float(w), max(x1, x2)),
                min(float(h), max(y1, y2)),
            ])
            prompt_labels.append(1)

        pos_pts: list[tuple[int, int]] = []
        neg_pts: list[tuple[int, int]] = []
        if points:
            r = max(12.0, min(w, h) * 0.03)
            labels = point_labels or [1] * len(points)
            for pt, lbl in zip(points, labels, strict=False):
                px, py = float(pt[0]), float(pt[1])
                ix = max(0, min(w - 1, int(round(px))))
                iy = max(0, min(h - 1, int(round(py))))
                if int(lbl) == 1:
                    pos_pts.append((ix, iy))
                else:
                    neg_pts.append((ix, iy))
                prompt_boxes.append([
                    max(0.0, px - r),
                    max(0.0, py - r),
                    min(float(w), px + r),
                    min(float(h), py + r),
                ])
                prompt_labels.append(1 if int(lbl) == 1 else 0)

        t0 = time.time()
        inputs = self._processor(
            images=img,
            input_boxes=[prompt_boxes],
            input_boxes_labels=[prompt_labels],
            return_tensors="pt",
        )
        with torch.no_grad():
            outputs = self._model(**inputs)

        results = self._processor.post_process_instance_segmentation(
            outputs,
            threshold=0.05,
            mask_threshold=0.5,
            target_sizes=inputs.get("original_sizes").tolist(),
        )[0]

        masks = results.get("masks")
        boxes = results.get("boxes")
        scores = results.get("scores")

        best_instance: Instance | None = None
        best_rank: tuple[int, int, float] = (-999, 0, -1.0)

        n_cands = len(scores) if scores is not None else 0
        for i in range(n_cands):
            mask = masks[i].cpu().numpy().astype(np.uint8)
            polygon = _mask_to_polygon(mask)
            if not polygon:
                continue
            score = float(scores[i])
            pos_hits = sum(1 for (ix, iy) in pos_pts if mask[iy, ix] > 0)
            neg_hits = sum(1 for (ix, iy) in neg_pts if mask[iy, ix] > 0)
            in_box_ok = 1
            if box and len(box) >= 4:
                bx1 = max(0, min(w - 1, int(round(min(box[0], box[2])))))
                by1 = max(0, min(h - 1, int(round(min(box[1], box[3])))))
                bx2 = max(bx1 + 1, min(w, int(round(max(box[0], box[2])))))
                by2 = max(by1 + 1, min(h, int(round(max(box[1], box[3])))))
                total_area = float(mask.sum()) or 1.0
                inside_area = float(mask[by1:by2, bx1:bx2].sum())
                in_box_ok = 1 if (inside_area / total_area) >= 0.5 else 0

            rank = (pos_hits - neg_hits, in_box_ok, score)
            if rank > best_rank:
                best_rank = rank
                best_instance = Instance(
                    polygon=polygon,
                    bbox=_xyxy_to_xywh([float(v) for v in boxes[i]]),
                    score=score,
                    mask_rle=_encode_rle(mask),
                )

        log.info(
            "sam3_predict",
            extra={
                "n_points": len(points or []),
                "has_box": bool(box),
                "score": best_instance.score if best_instance else 0.0,
                "duration_ms": round((time.time() - t0) * 1000, 2),
            },
        )
        if best_instance is None:
            return Instance(polygon=[], bbox=[0.0, 0.0, 0.0, 0.0], score=0.0, mask_rle=None)
        return best_instance

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
