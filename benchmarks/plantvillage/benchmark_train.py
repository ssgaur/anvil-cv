"""
Benchmark SAM2 fine-tuning across configs and hardware tiers.

Configs:
  vanilla        no training; eval-only baseline
  lora_r8        LoRA on mask decoder, rank 8 (~0.5 M trainable params)
  lora_r16       LoRA on mask decoder, rank 16 (~1.0 M)
  full_decoder   full fine-tune of mask decoder only (~4 M)
  full           full fine-tune of decoder + image encoder (~80 M, biggest, paid-tier)

Variants:
  base_plus      sam2.1_hiera_b+  (default; the one we ship)
  tiny           sam2.1_hiera_tiny  (only variant that fits 16GB VRAM for full)

Each run writes results/<config>_<variant>_<gpu>_<utc>.json with:
  - loss per training step
  - peak GPU/MPS memory (GB)
  - wall-clock seconds
  - mean IoU on the eval split
  - per-class IoU
  - hardware tag (auto-detected)

Usage:
  uv run python benchmarks/plantvillage/benchmark_train.py --config lora_r8
  uv run python benchmarks/plantvillage/benchmark_train.py --config full --variant tiny --max-steps 500

Prereqs:
  - dataset built: python benchmarks/plantvillage/seed_data.py --count 1000
  - SAM2 checkpoint at ./checkpoints/sam2.1_hiera_*.pt
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import sys
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image

# Make the repo's apps/ml importable
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "ml"))

from anvil_ml.training import (  # noqa: E402
    _decode_mask,
    _forward_train,
    dice_bce_loss,
    mask_iou,
    sample_point_in_mask,
)

VARIANTS: dict[str, dict[str, str]] = {
    "base_plus": {
        "ckpt": "checkpoints/sam2.1_hiera_base_plus.pt",
        "cfg": "configs/sam2.1/sam2.1_hiera_b+.yaml",
    },
    "tiny": {
        "ckpt": "checkpoints/sam2.1_hiera_tiny.pt",
        "cfg": "configs/sam2.1/sam2.1_hiera_t.yaml",
    },
}


@dataclass
class RunMeta:
    config: str
    variant: str
    device: str
    device_name: str
    platform: str
    n_train: int
    n_eval: int
    max_steps: int
    started_utc: str


def auto_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def device_name(d: torch.device) -> str:
    if d.type == "cuda":
        return torch.cuda.get_device_name(d)
    if d.type == "mps":
        return f"Apple Silicon ({platform.machine()})"
    return platform.processor() or "cpu"


def peak_memory_gb(d: torch.device) -> float:
    if d.type == "cuda":
        return torch.cuda.max_memory_allocated(d) / 1024**3
    if d.type == "mps":
        # MPS doesn't expose peak; report current driver allocation as best-effort
        try:
            return torch.mps.driver_allocated_memory() / 1024**3
        except Exception:  # noqa: BLE001
            return 0.0
    return 0.0


def reset_peak_memory(d: torch.device) -> None:
    if d.type == "cuda":
        torch.cuda.reset_peak_memory_stats(d)
    elif d.type == "mps":
        with suppress(Exception):
            torch.mps.empty_cache()


def load_pair(image_path: Path, mask_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (image_rgb_uint8 HxWx3, mask_uint8 HxW with 0/1)."""
    img = np.array(Image.open(image_path).convert("RGB"))
    msk = np.array(Image.open(mask_path).convert("L"))
    msk = (msk > 127).astype(np.uint8)
    return img, msk


def class_of(filename: str) -> str:
    return filename.split("__")[0]


def setup_trainable(model: torch.nn.Module, config: str) -> tuple[list[torch.nn.Parameter], int]:
    """Configure which params get gradients and return them. Returns (params, n_trainable)."""
    # Always start by freezing everything for clarity
    for p in model.parameters():
        p.requires_grad = False

    if config in {"lora_r8", "lora_r16"}:
        from anvil_ml.lora import freeze_all_but_lora, inject_lora, lora_parameters
        rank = 8 if config == "lora_r8" else 16
        alpha = rank * 2
        inject_lora(model.sam_mask_decoder, r=rank, alpha=alpha)
        freeze_all_but_lora(model)
        params = list(lora_parameters(model.sam_mask_decoder))
    elif config == "full_decoder":
        for p in model.sam_mask_decoder.parameters():
            p.requires_grad = True
        params = [p for p in model.sam_mask_decoder.parameters() if p.requires_grad]
    elif config == "full":
        for p in model.sam_mask_decoder.parameters():
            p.requires_grad = True
        for p in model.image_encoder.parameters():
            p.requires_grad = True
        params = [p for p in model.parameters() if p.requires_grad]
    elif config == "vanilla":
        params = []
    else:
        raise ValueError(f"unknown config: {config}")

    n_trainable = sum(p.numel() for p in params)
    return params, n_trainable


def encode_image(predictor, img: np.ndarray) -> tuple[dict[str, Any], tuple[int, int]]:
    """Run SAM2 image encoder and return cached features dict + original HW.

    For full fine-tune we want gradients through the encoder. For LoRA / decoder-only
    we don't, so we use predictor.set_image() which runs no_grad internally.
    """
    predictor.set_image(img)
    return predictor._features, predictor._orig_hw[0]


def encode_image_with_grad(model, transforms, img: np.ndarray, device: torch.device):
    """Like encode_image but keeps grad through image encoder (for full fine-tune)."""
    # Mirror what SAM2ImagePredictor.set_image does, minus the no_grad wrapping
    img_tensor = transforms(img).unsqueeze(0).to(device)  # (1,3,1024,1024)
    backbone_out = model.forward_image(img_tensor)
    _, vision_feats, _, _ = model._prepare_backbone_features(backbone_out)
    if model.directly_add_no_mem_embed:
        vision_feats[-1] = vision_feats[-1] + model.no_mem_embed
    feats = {
        "image_embed": vision_feats[-1].permute(1, 2, 0).view(1, -1, 64, 64),
        "high_res_feats": [
            f.permute(1, 2, 0).view(1, -1, *s)
            for f, s in zip(vision_feats[:-1], model._bb_feat_sizes[:-1], strict=False)
        ],
    }
    return feats, img.shape[:2]


def train_one_step(
    model, transforms, predictor, img: np.ndarray, gt_mask: np.ndarray,
    device: torch.device, rng: random.Random, with_encoder_grad: bool,
) -> float:
    """Run one training step on a single (image, mask) sample. Returns scalar loss."""
    if with_encoder_grad:
        feats, orig_hw = encode_image_with_grad(model, transforms.transforms, img, device)
    else:
        with torch.no_grad():
            feats, orig_hw = encode_image(predictor, img)

    pts = sample_point_in_mask(gt_mask, n=1, rng=rng)
    if not pts:
        return 0.0
    sparse, dense = _forward_train(model, transforms, pts[0], orig_hw, device)
    low_res, _ = _decode_mask(model, feats, sparse, dense, multimask=False)
    hires = transforms.postprocess_masks(low_res, orig_hw)  # (1,1,H,W) logits
    target = torch.from_numpy(gt_mask).to(device).float().unsqueeze(0).unsqueeze(0)
    loss = dice_bce_loss(hires, target)
    return loss


@torch.no_grad()
def evaluate(predictor, eval_pairs: list[tuple[Path, Path, str]]) -> dict[str, Any]:
    """Run prompted inference on each eval pair, return mean IoU + per-class."""
    per_item: list[dict] = []
    by_class: dict[str, list[float]] = {}
    for img_p, msk_p, cls in eval_pairs:
        img, gt = load_pair(img_p, msk_p)
        predictor.set_image(img)
        # Pick the GT centroid as a single foreground point prompt
        ys, xs = np.where(gt > 0)
        if len(ys) == 0:
            continue
        cy, cx = float(np.mean(ys)), float(np.mean(xs))
        masks, scores, _ = predictor.predict(
            point_coords=np.array([[cx, cy]]),
            point_labels=np.array([1]),
            multimask_output=False,
        )
        pred = (masks[0] > 0.5).astype(np.uint8)
        iou = mask_iou(pred, gt.astype(np.uint8))
        per_item.append({"file": img_p.name, "class": cls, "iou": iou})
        by_class.setdefault(cls, []).append(iou)

    per_class = {c: float(np.mean(v)) for c, v in by_class.items()}
    mean_iou = float(np.mean([p["iou"] for p in per_item])) if per_item else 0.0
    return {"mean_iou": mean_iou, "per_class_iou": per_class, "n": len(per_item)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, choices=["vanilla", "lora_r8", "lora_r16", "full_decoder", "full"])
    parser.add_argument("--variant", default="base_plus", choices=list(VARIANTS))
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--train-images", type=int, default=500)
    parser.add_argument("--eval-images", type=int, default=100)
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "dataset")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "results")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--gradient-checkpointing", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)

    # Device
    device = auto_device() if args.device == "auto" else torch.device(args.device)
    reset_peak_memory(device)
    dname = device_name(device)
    print(f"[bench] device={device} ({dname})  config={args.config}  variant={args.variant}")

    # Dataset
    splits = json.loads((args.dataset / "splits.json").read_text())
    if not splits["train"] or not splits["eval"]:
        sys.exit(f"empty splits in {args.dataset}/splits.json — re-run seed_data.py")
    images_dir = args.dataset / "images"
    masks_dir = args.dataset / "masks"

    def to_pairs(filenames: list[str]) -> list[tuple[Path, Path, str]]:
        pairs = []
        for f in filenames:
            ip = images_dir / f
            mp = masks_dir / f.replace(".jpg", ".png")
            if ip.exists() and mp.exists():
                pairs.append((ip, mp, class_of(f)))
        return pairs

    rng.shuffle(splits["train"])
    rng.shuffle(splits["eval"])
    train_pairs = to_pairs(splits["train"])[: args.train_images]
    eval_pairs = to_pairs(splits["eval"])[: args.eval_images]
    print(f"[bench] train={len(train_pairs)} eval={len(eval_pairs)}")

    # Model
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    v = VARIANTS[args.variant]
    ckpt = os.getenv("SAM2_CHECKPOINT", v["ckpt"])
    cfg_yaml = os.getenv("SAM2_MODEL_CFG", v["cfg"])
    ckpt_abs = str(Path(ckpt).resolve())
    print(f"[bench] loading {args.variant} from {ckpt_abs} ...")
    model = build_sam2(cfg_yaml, ckpt_abs, device=str(device))
    predictor = SAM2ImagePredictor(model)
    transforms = predictor._transforms

    if args.gradient_checkpointing and hasattr(model.image_encoder, "gradient_checkpointing_enable"):
        model.image_encoder.gradient_checkpointing_enable()

    # Trainable params
    params, n_trainable = setup_trainable(model, args.config)
    print(f"[bench] trainable params: {n_trainable:,}")

    # Eval BEFORE training (vanilla baseline)
    pre = evaluate(predictor, eval_pairs)
    print(f"[bench] pre-train mean_iou={pre['mean_iou']:.4f}")

    meta = RunMeta(
        config=args.config,
        variant=args.variant,
        device=str(device),
        device_name=dname,
        platform=platform.platform(),
        n_train=len(train_pairs),
        n_eval=len(eval_pairs),
        max_steps=args.max_steps,
        started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    results: dict[str, Any] = {
        "meta": asdict(meta),
        "n_trainable": n_trainable,
        "pre_train_mean_iou": pre["mean_iou"],
        "pre_train_per_class_iou": pre["per_class_iou"],
        "loss_per_step": [],
        "wall_seconds": 0.0,
        "peak_memory_gb": 0.0,
        "post_train_mean_iou": None,
        "post_train_per_class_iou": None,
    }

    if args.config == "vanilla":
        results["wall_seconds"] = 0.0
        results["post_train_mean_iou"] = pre["mean_iou"]
        results["post_train_per_class_iou"] = pre["per_class_iou"]
    else:
        with_encoder_grad = args.config == "full"
        optim = torch.optim.AdamW(params, lr=args.lr)
        model.train()

        t0 = time.time()
        step = 0
        while step < args.max_steps:
            img_p, msk_p, _cls = train_pairs[step % len(train_pairs)]
            img, gt = load_pair(img_p, msk_p)
            try:
                loss = train_one_step(
                    model, transforms, predictor, img, gt, device, rng, with_encoder_grad,
                )
                if isinstance(loss, torch.Tensor):
                    optim.zero_grad()
                    loss.backward()
                    optim.step()
                    results["loss_per_step"].append(round(float(loss.detach().cpu()), 5))
                step += 1
                if step % 25 == 0 or step == args.max_steps:
                    avg = float(np.mean(results["loss_per_step"][-25:])) if results["loss_per_step"] else 0.0
                    elapsed = time.time() - t0
                    rate = step / elapsed if elapsed > 0 else 0
                    print(f"[bench] step {step:>4}/{args.max_steps}  loss={avg:.4f}  ({rate:.2f} step/s)")
            except torch.cuda.OutOfMemoryError as e:
                results["wall_seconds"] = time.time() - t0
                results["oom"] = str(e)
                print(f"[bench] OOM at step {step}: {e}")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[bench] skip step {step}: {e}")
                step += 1

        results["wall_seconds"] = round(time.time() - t0, 2)
        results["peak_memory_gb"] = round(peak_memory_gb(device), 3)

        # Post-train eval
        post = evaluate(predictor, eval_pairs)
        results["post_train_mean_iou"] = post["mean_iou"]
        results["post_train_per_class_iou"] = post["per_class_iou"]
        print(f"[bench] post-train mean_iou={post['mean_iou']:.4f}")

    # Persist
    safe_dev = device.type
    fname = f"{args.config}_{args.variant}_{safe_dev}_{int(time.time())}.json"
    out_path = args.out / fname
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\n[bench] wrote {out_path}")
    print(f"[bench] summary: pre={results['pre_train_mean_iou']:.4f}  "
          f"post={results['post_train_mean_iou']:.4f}  "
          f"wall={results['wall_seconds']:.0f}s  "
          f"peak_mem={results['peak_memory_gb']:.2f}GB  "
          f"trainable={n_trainable:,}")


if __name__ == "__main__":
    main()
