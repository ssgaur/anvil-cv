"""SAM2 inference engine. Loads model once, exposes embed() and predict()."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

# Import SAM2 lazily to avoid heavy load at import time when running tests
_lock = threading.Lock()
_predictor = None
_model = None


def _device() -> torch.device:
    pref = os.getenv("ML_DEVICE", "auto").lower()
    if pref == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    if pref == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    if pref == "cpu":
        return torch.device("cpu")
    # auto
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def get_predictor():
    global _predictor, _model
    with _lock:
        if _predictor is not None:
            return _predictor
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        ckpt = os.getenv("SAM2_CHECKPOINT", "./checkpoints/sam2.1_hiera_base_plus.pt")
        cfg = os.getenv("SAM2_MODEL_CFG", "configs/sam2.1/sam2.1_hiera_b+.yaml")
        ckpt_abs = str(Path(ckpt).resolve())
        dev = _device()
        print(f"[anvil-ml] loading SAM2 ckpt={ckpt_abs} device={dev}")
        t0 = time.time()
        _model = build_sam2(cfg, ckpt_abs, device=str(dev))
        _predictor = SAM2ImagePredictor(_model)
        print(f"[anvil-ml] SAM2 loaded in {time.time()-t0:.2f}s")
        return _predictor


def device_name() -> str:
    return str(_device())


def _load_image(image_path: str) -> np.ndarray:
    return np.array(Image.open(image_path).convert("RGB"))


def embed(image_path: str, embedding_path: str) -> dict:
    """Run SAM2 image encoder on `image_path`, save embedding to `embedding_path` (.npz)."""
    p = get_predictor()
    img = _load_image(image_path)
    t0 = time.time()
    p.set_image(img)
    elapsed = time.time() - t0

    # SAM2 stores per-image features on the predictor itself (`_features`, `_orig_hw`, `_is_image_set`).
    # Persist a minimal payload — features can be a dict of tensors. We move to CPU and save as .npz.
    feats = p._features  # type: ignore[attr-defined]
    orig_hw = p._orig_hw  # type: ignore[attr-defined]

    payload: dict[str, np.ndarray] = {}
    if isinstance(feats, dict):
        for k, v in feats.items():
            if isinstance(v, torch.Tensor):
                payload[f"f_{k}"] = v.detach().cpu().numpy()
            elif isinstance(v, list):
                for i, t in enumerate(v):
                    payload[f"f_{k}_{i}"] = t.detach().cpu().numpy()
    else:
        # Some versions: feats is a list of tensors
        for i, t in enumerate(feats):
            payload[f"f_{i}"] = t.detach().cpu().numpy()

    payload["orig_hw"] = np.array(orig_hw, dtype=np.int64)
    payload["img_h"] = np.array([img.shape[0]], dtype=np.int64)
    payload["img_w"] = np.array([img.shape[1]], dtype=np.int64)

    Path(embedding_path).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(embedding_path, **payload)
    return {
        "ok": True,
        "embed_seconds": round(elapsed, 3),
        "embedding_path": embedding_path,
        "device": device_name(),
        "image_size": [img.shape[1], img.shape[0]],
    }


def _restore_features(p, embedding_path: str) -> None:
    """Re-hydrate predictor state from a saved .npz so we skip re-encoding."""
    data = np.load(embedding_path, allow_pickle=False)
    dev = _device()
    feats: dict | list = {}
    grouped: dict[str, dict[int, np.ndarray]] = {}
    flat: dict[str, np.ndarray] = {}
    for k in data.files:
        if k in {"orig_hw", "img_h", "img_w"}:
            continue
        if k.startswith("f_"):
            rest = k[2:]
            if "_" in rest and rest.rsplit("_", 1)[1].isdigit():
                base, idx = rest.rsplit("_", 1)
                grouped.setdefault(base, {})[int(idx)] = data[k]
            else:
                flat[rest] = data[k]
    if grouped or flat:
        feats = {}
        for base, parts in grouped.items():
            feats[base] = [
                torch.from_numpy(parts[i]).to(dev) for i in sorted(parts.keys())
            ]
        for k, v in flat.items():
            feats[k] = torch.from_numpy(v).to(dev)
    p._features = feats  # type: ignore[attr-defined]
    p._orig_hw = [tuple(row) for row in data["orig_hw"].tolist()]  # type: ignore[attr-defined]
    p._is_image_set = True  # type: ignore[attr-defined]
    p._is_batch = False  # type: ignore[attr-defined]


def predict(
    image_path: str,
    embedding_path: str | None,
    points: list[list[float]] | None = None,
    point_labels: list[int] | None = None,
    box: list[float] | None = None,
) -> dict:
    """Returns the best mask as a polygon + bbox + score."""
    import cv2

    p = get_predictor()
    if embedding_path and Path(embedding_path).exists():
        try:
            _restore_features(p, embedding_path)
        except Exception as e:
            print(f"[anvil-ml] failed to restore embedding ({e}); re-embedding")
            p.set_image(_load_image(image_path))
    else:
        p.set_image(_load_image(image_path))

    point_coords = np.array(points, dtype=np.float32) if points else None
    labels = np.array(point_labels, dtype=np.int64) if point_labels else None
    box_arr = np.array(box, dtype=np.float32) if box else None

    masks, scores, _ = p.predict(
        point_coords=point_coords,
        point_labels=labels,
        box=box_arr,
        multimask_output=True,
    )
    best_idx = int(np.argmax(scores))
    mask = masks[best_idx].astype(np.uint8) * 255
    score = float(scores[best_idx])

    # Mask -> simplified polygon (largest contour)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return {"polygon": [], "score": score, "bbox": [0, 0, 0, 0]}
    cnt = max(contours, key=cv2.contourArea)
    epsilon = 0.0015 * cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, epsilon, True)
    polygon = [[float(pt[0][0]), float(pt[0][1])] for pt in approx]
    x, y, w, h = cv2.boundingRect(cnt)
    return {
        "polygon": polygon,
        "score": score,
        "bbox": [float(x), float(y), float(w), float(h)],
    }
