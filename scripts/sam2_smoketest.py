"""Smoke test: load SAM2, embed an image, predict mask from a point. MPS device."""
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT = ROOT / "checkpoints" / "sam2.1_hiera_base_plus.pt"
CONFIG = "configs/sam2.1/sam2.1_hiera_b+.yaml"
IMG = ROOT / "tmp" / "cucumber.jpg"

device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
print(f"device={device}")

from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

t0 = time.time()
model = build_sam2(CONFIG, str(CHECKPOINT), device=str(device))
print(f"build_sam2: {time.time()-t0:.2f}s")

predictor = SAM2ImagePredictor(model)

img = np.array(Image.open(IMG).convert("RGB"))
print(f"image shape: {img.shape}")

t0 = time.time()
predictor.set_image(img)
print(f"set_image (embedding): {time.time()-t0:.2f}s")

H, W = img.shape[:2]
point = np.array([[W // 2, H // 2]])
labels = np.array([1])

t0 = time.time()
masks, scores, _ = predictor.predict(
    point_coords=point, point_labels=labels, multimask_output=True
)
print(f"predict: {time.time()-t0:.3f}s")
print(f"masks: {masks.shape}, scores: {scores}")
print(f"best mask area: {masks[scores.argmax()].sum()} px")

best = masks[scores.argmax()].astype(np.uint8) * 255
Image.fromarray(best).save(ROOT / "tmp" / "mask.png")
print(f"wrote {ROOT / 'tmp' / 'mask.png'}")
print("OK ✓")
sys.exit(0)
