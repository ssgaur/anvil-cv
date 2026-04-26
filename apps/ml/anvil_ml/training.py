"""SAM2 mask-decoder LoRA fine-tuning loop.

Designed for solo / M1 use:
- Image encoder is FROZEN (we use the cached .npz embeddings already on disk).
- Prompt encoder is FROZEN.
- Mask decoder gets LoRA adapters on attention projections; only those train.
- Loss = BCE-with-logits + Dice on the predicted mask vs. the ground-truth
  binary mask rasterized from the polygon/bbox annotation.
- Eval = mean IoU on a held-out slice (best of multimask_output).

Inputs:
- A list of (image_path, embedding_path, [annotations]) where each annotation
  has geometry_type, geometry, and a class label.

Outputs:
- Saved adapter file (`.pt`) at adapter_path.
- A metrics dict (train losses, val IoU per epoch).
"""
from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from anvil_ml.engine import _device, _restore_features
from anvil_ml.lora import (
    freeze_all_but_lora,
    inject_lora,
    lora_parameters,
    save_lora,
)


@dataclass
class TrainConfig:
    epochs: int = 20
    lr: float = 1e-4
    rank: int = 8
    alpha: int = 16
    eval_split: float = 0.2
    points_per_mask: int = 1
    seed: int = 42
    device: str = "cpu"  # "mps" can be flaky for backward; default cpu


@dataclass
class TrainItem:
    image_path: str
    embedding_path: str
    annotations: list[dict[str, Any]] = field(default_factory=list)
    # each ann: {"geometry_type": "polygon"|"bbox", "geometry": {...}, "image_w": int, "image_h": int}


# --------------- mask rasterization ---------------
def rasterize_annotation(ann: dict[str, Any], h: int, w: int) -> np.ndarray | None:
    """Return a uint8 (0/1) HxW mask from the annotation, or None if invalid."""
    g = ann["geometry"]
    mask = np.zeros((h, w), dtype=np.uint8)
    if ann["geometry_type"] == "polygon":
        pts = g.get("points") or []
        if len(pts) < 3:
            return None
        poly = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(mask, [poly], color=1)
    elif ann["geometry_type"] == "bbox":
        x = int(g["x"]); y = int(g["y"])
        ww = int(g["w"]); hh = int(g["h"])
        x2, y2 = max(0, x), max(0, y)
        cv2.rectangle(mask, (x2, y2), (x2 + ww, y2 + hh), 1, thickness=-1)
    else:
        return None
    return mask


def sample_point_in_mask(mask: np.ndarray, n: int = 1, rng: random.Random | None = None) -> list[tuple[float, float]]:
    """Return up to n (x, y) points sampled uniformly inside mask>0; falls back to centroid."""
    rng = rng or random.Random(0)
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return []
    if n == 1:
        # Use a random interior point (helps generalization vs. always-centroid)
        i = rng.randrange(len(xs))
        return [(float(xs[i]), float(ys[i]))]
    idxs = rng.sample(range(len(xs)), min(n, len(xs)))
    return [(float(xs[i]), float(ys[i])) for i in idxs]


# --------------- losses ---------------
def dice_bce_loss(pred_logits: torch.Tensor, target: torch.Tensor, eps: float = 1e-6) -> torch.Tensor:
    """pred_logits: (B,1,H,W); target: (B,1,H,W) in [0,1]."""
    bce = F.binary_cross_entropy_with_logits(pred_logits, target)
    p = torch.sigmoid(pred_logits)
    inter = (p * target).sum()
    union = p.sum() + target.sum()
    dice = 1 - (2 * inter + eps) / (union + eps)
    return bce + dice


def mask_iou(pred_bin: np.ndarray, gt_bin: np.ndarray) -> float:
    inter = float(((pred_bin > 0) & (gt_bin > 0)).sum())
    union = float(((pred_bin > 0) | (gt_bin > 0)).sum())
    return inter / union if union > 0 else 0.0


# --------------- forward (training) ---------------
def _forward_train(
    model: torch.nn.Module,
    transforms,
    point_xy: tuple[float, float],
    orig_hw: tuple[int, int],
    device: torch.device,
):
    """Run prompt encoder + mask decoder for a single point prompt.

    Returns: (low_res_logits, hires_mask_logits) — both with batch dim.
    """
    coords = torch.tensor([[list(point_xy)]], dtype=torch.float32, device=device)  # (1,1,2)
    coords_t = transforms.transform_coords(coords, normalize=True, orig_hw=orig_hw)
    labels_t = torch.tensor([[1]], dtype=torch.int32, device=device)  # foreground

    sparse, dense = model.sam_prompt_encoder(
        points=(coords_t, labels_t), boxes=None, masks=None,
    )
    return sparse, dense


def _decode_mask(model, features, sparse, dense, multimask: bool):
    """Run mask decoder. Returns (low_res_masks, iou_predictions)."""
    high_res = [feat[0:1] for feat in features["high_res_feats"]]
    img_embed = features["image_embed"][0:1]
    low_res, iou_pred, _, _ = model.sam_mask_decoder(
        image_embeddings=img_embed,
        image_pe=model.sam_prompt_encoder.get_dense_pe(),
        sparse_prompt_embeddings=sparse,
        dense_prompt_embeddings=dense,
        multimask_output=multimask,
        repeat_image=False,
        high_res_features=high_res,
    )
    return low_res, iou_pred


# --------------- main loop ---------------
def train_lora(
    items: list[TrainItem],
    adapter_path: str,
    cfg: TrainConfig,
    on_progress: Callable[[float, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Train a LoRA adapter on the mask decoder. Returns a metrics dict."""
    rng = random.Random(cfg.seed)
    device = torch.device(cfg.device)
    print(f"[train] device={device} items={len(items)} epochs={cfg.epochs}")

    # 1. Build a fresh predictor and inject LoRA — we DO NOT mutate the global
    #    inference predictor, so concurrent /predict calls keep working.
    import os

    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    ckpt = os.getenv("SAM2_CHECKPOINT", "./checkpoints/sam2.1_hiera_base_plus.pt")
    cfg_yaml = os.getenv("SAM2_MODEL_CFG", "configs/sam2.1/sam2.1_hiera_b+.yaml")
    ckpt_abs = str(Path(ckpt).resolve())
    model = build_sam2(cfg_yaml, ckpt_abs, device=str(device))
    predictor = SAM2ImagePredictor(model)
    transforms = predictor._transforms

    wrapped = inject_lora(model.sam_mask_decoder, r=cfg.rank, alpha=cfg.alpha)
    freeze_all_but_lora(model)
    n_lora = sum(p.numel() for p in lora_parameters(model.sam_mask_decoder))
    print(f"[train] LoRA wrapped={len(wrapped)} layers · trainable params={n_lora:,}")

    # Make sure other components stay frozen (defense in depth)
    for p in model.image_encoder.parameters():
        p.requires_grad = False
    for p in model.sam_prompt_encoder.parameters():
        p.requires_grad = False

    optim = torch.optim.AdamW(list(lora_parameters(model.sam_mask_decoder)), lr=cfg.lr)

    # 2. Flatten (item, ann) pairs, drop invalid ones
    flat: list[tuple[TrainItem, dict[str, Any], np.ndarray]] = []
    for it in items:
        for ann in it.annotations:
            mask = rasterize_annotation(ann, ann["image_h"], ann["image_w"])
            if mask is None or mask.sum() < 16:
                continue
            flat.append((it, ann, mask))
    if not flat:
        raise RuntimeError("no valid annotations to train on")

    rng.shuffle(flat)
    n_eval = max(1, int(len(flat) * cfg.eval_split)) if cfg.eval_split > 0 else 0
    eval_set = flat[:n_eval]
    train_set = flat[n_eval:]
    print(f"[train] train={len(train_set)} eval={len(eval_set)}")

    metrics: dict[str, Any] = {
        "train_loss_per_epoch": [],
        "val_iou_per_epoch": [],
        "n_train": len(train_set),
        "n_eval": len(eval_set),
        "n_lora_params": n_lora,
        "wrapped_layers": len(wrapped),
        "device": str(device),
        "started_at": time.time(),
    }

    # 3. Training loop
    for epoch in range(cfg.epochs):
        model.train()
        rng.shuffle(train_set)
        epoch_loss = 0.0
        n = 0
        for it, _ann, gt_mask in train_set:
            try:
                _restore_features(predictor, it.embedding_path)
                # Move features to device with grad off
                with torch.no_grad():
                    feats = {
                        k: ([t.to(device) for t in v] if isinstance(v, list) else v.to(device))
                        for k, v in predictor._features.items()
                    }
                orig_hw = predictor._orig_hw[0]

                pts = sample_point_in_mask(gt_mask, n=cfg.points_per_mask, rng=rng)
                if not pts:
                    continue

                sparse, dense = _forward_train(model, transforms, pts[0], orig_hw, device)
                low_res, _ = _decode_mask(model, feats, sparse, dense, multimask=False)
                # Upscale to original size — postprocess_masks returns LOGITS (we set return_logits behavior here)
                hires = transforms.postprocess_masks(low_res, orig_hw)  # (1,1,H,W) logits
                target = torch.from_numpy(gt_mask).to(device).float().unsqueeze(0).unsqueeze(0)
                loss = dice_bce_loss(hires, target)

                optim.zero_grad()
                loss.backward()
                optim.step()
                epoch_loss += float(loss.detach().cpu())
                n += 1
            except Exception as e:  # don't kill the whole run on one bad sample
                print(f"[train] skip sample due to error: {e}")
                continue

        avg_loss = epoch_loss / max(1, n)
        # 4. Eval
        ev = _evaluate(model, predictor, transforms, eval_set, device) if eval_set else {"mean_iou": 0.0, "per_class_iou": {}, "per_item": []}
        val_iou = ev["mean_iou"]
        metrics["train_loss_per_epoch"].append(round(avg_loss, 6))
        metrics["val_iou_per_epoch"].append(round(val_iou, 4))
        metrics["val_iou_per_class"] = ev["per_class_iou"]  # last epoch wins
        metrics["last_eval_per_item"] = ev["per_item"]
        progress = (epoch + 1) / cfg.epochs
        print(f"[train] epoch {epoch + 1}/{cfg.epochs}  loss={avg_loss:.4f}  val_iou={val_iou:.4f}")
        if on_progress:
            try:
                on_progress(progress, {"epoch": epoch + 1, "loss": avg_loss, "val_iou": val_iou})
            except Exception as e:
                print(f"[train] on_progress hook failed: {e}")

    # 5. Save adapter
    save_lora(model.sam_mask_decoder, adapter_path)
    metrics["completed_at"] = time.time()
    metrics["adapter_path"] = adapter_path
    metrics["base_val_iou"] = metrics["val_iou_per_epoch"][0] if metrics["val_iou_per_epoch"] else None
    metrics["final_val_iou"] = metrics["val_iou_per_epoch"][-1] if metrics["val_iou_per_epoch"] else None
    print(f"[train] done · saved adapter to {adapter_path}")
    print(f"[train] metrics: {json.dumps({k: v for k, v in metrics.items() if 'per_epoch' not in k})}")

    # Free memory
    del model, predictor
    if device.type == "mps":
        torch.mps.empty_cache()
    elif device.type == "cuda":
        torch.cuda.empty_cache()

    return metrics


@torch.no_grad()
def _evaluate(model, predictor, transforms, eval_set, device) -> dict[str, Any]:
    """Returns {mean_iou, per_class_iou: {name: iou}, per_item: [...]}.

    eval_set items are tuples (TrainItem, ann_dict, gt_mask).  ann_dict may carry
    optional `class_id` / `class_name` keys; missing class info is bucketed as
    `_unlabeled`.
    """
    out = {"mean_iou": 0.0, "per_class_iou": {}, "per_item": []}
    if not eval_set:
        return out
    model.eval()
    ious: list[float] = []
    by_class: dict[str, list[float]] = {}
    for it, ann, gt_mask in eval_set:
        try:
            _restore_features(predictor, it.embedding_path)
            feats = {
                k: ([t.to(device) for t in v] if isinstance(v, list) else v.to(device))
                for k, v in predictor._features.items()
            }
            orig_hw = predictor._orig_hw[0]
            pts = sample_point_in_mask(gt_mask, n=1, rng=random.Random(123))
            if not pts:
                continue
            sparse, dense = _forward_train(model, transforms, pts[0], orig_hw, device)
            low_res, iou_pred = _decode_mask(model, feats, sparse, dense, multimask=True)
            hires = transforms.postprocess_masks(low_res, orig_hw)
            best = int(iou_pred[0].argmax().item())
            pred_bin = (hires[0, best] > 0).cpu().numpy().astype(np.uint8)
            iou_val = mask_iou(pred_bin, gt_mask)
        except Exception as e:
            print(f"[eval] skip due to error: {e}")
            continue
        cname = str(ann.get("class_name") or ann.get("class_id") or "_unlabeled")
        ious.append(iou_val)
        by_class.setdefault(cname, []).append(iou_val)
        out["per_item"].append({
            "image_path": it.image_path,
            "class_id": ann.get("class_id"),
            "class_name": cname,
            "iou": round(iou_val, 4),
        })
    out["mean_iou"] = float(np.mean(ious)) if ious else 0.0
    out["per_class_iou"] = {k: round(float(np.mean(v)), 4) for k, v in by_class.items()}
    return out


# --------------- adapter cache for inference ---------------
_adapter_predictors: dict[str, Any] = {}


def evaluate_with_adapter(
    items: list[TrainItem],
    adapter_path: str | None,
    device_str: str = "cpu",
) -> dict[str, Any]:
    """Run eval (no training) on the given items, optionally with a LoRA adapter
    loaded onto the mask decoder. Returns same shape as `_evaluate`.

    Used by the model-version A/B compare endpoint.
    """
    import os

    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    from anvil_ml.lora import load_lora
    device = torch.device(device_str)
    ckpt = os.getenv("SAM2_CHECKPOINT", "./checkpoints/sam2.1_hiera_base_plus.pt")
    cfg_yaml = os.getenv("SAM2_MODEL_CFG", "configs/sam2.1/sam2.1_hiera_b+.yaml")
    ckpt_abs = str(Path(ckpt).resolve())
    model = build_sam2(cfg_yaml, ckpt_abs, device=str(device))
    predictor = SAM2ImagePredictor(model)
    transforms = predictor._transforms
    if adapter_path:
        # load_lora injects matching LoRA modules itself.
        load_lora(model.sam_mask_decoder, adapter_path)

    # Build eval flat list
    flat: list[tuple[TrainItem, dict[str, Any], np.ndarray]] = []
    for it in items:
        for ann in it.annotations:
            mask = rasterize_annotation(ann, ann["image_h"], ann["image_w"])
            if mask is None or mask.sum() < 16:
                continue
            flat.append((it, ann, mask))
    out = _evaluate(model, predictor, transforms, flat, device)
    del model, predictor
    if device.type == "mps":
        torch.mps.empty_cache()
    elif device.type == "cuda":
        torch.cuda.empty_cache()
    return out


def get_adapter_predictor(adapter_path: str):
    """Return a SAM2ImagePredictor with the given LoRA adapter loaded.

    Caches per adapter_path so we don't rebuild on every /predict.
    """
    import os

    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    from anvil_ml.lora import load_lora

    cache_key = str(Path(adapter_path).resolve())
    if cache_key in _adapter_predictors:
        return _adapter_predictors[cache_key]

    ckpt = os.getenv("SAM2_CHECKPOINT", "./checkpoints/sam2.1_hiera_base_plus.pt")
    cfg = os.getenv("SAM2_MODEL_CFG", "configs/sam2.1/sam2.1_hiera_b+.yaml")
    dev = _device()
    print(f"[adapter] loading adapter={cache_key} on {dev}")
    model = build_sam2(cfg, str(Path(ckpt).resolve()), device=str(dev))
    n = load_lora(model.sam_mask_decoder, cache_key)
    print(f"[adapter] loaded {n} LoRA layers")
    p = SAM2ImagePredictor(model)
    _adapter_predictors[cache_key] = p
    return p
