"""
Build a SAM2-ready (image, binary mask) dataset from PlantVillage.

PlantVillage on the spMohanty GitHub mirror ships three views per leaf:
  raw/color/<class>/<file>.JPG       <- the input image
  raw/segmented/<class>/<file>.JPG   <- leaf cut out on black background
  raw/grayscale/<class>/<file>.JPG   <- not used here

We derive a binary leaf mask by thresholding the `segmented` view: any
pixel above an intensity floor is leaf, anything else is background.
Result: per-image (image.jpg, mask.png) pairs we can feed straight into
SAM2 fine-tuning -- no human labeling required.

Dataset usage:

  # quick prototype (5 classes x 20 images = 100 pairs, ~30s)
  uv run python benchmarks/plantvillage/seed_data.py

  # benchmark scale (5 classes x 1000 = 5,000 pairs, ~10 min)
  uv run python benchmarks/plantvillage/seed_data.py --count 1000

  # full bonus dataset (all 38 classes, ~50,000 pairs, ~30 min)
  uv run python benchmarks/plantvillage/seed_data.py --full

Output:
  benchmarks/plantvillage/dataset/
    images/<class>__<idx>.jpg   (color, 1024x1024 letterboxed)
    masks/<class>__<idx>.png    (uint8, 0=bg / 255=leaf, same dims)
    manifest.csv                (filename, class, source_filename)
    splits.json                 (deterministic train/eval/holdout split)
    LICENSE.txt
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageOps

GH_REPO = "spMohanty/PlantVillage-Dataset"
GH_REF = "master"
LIST_API = "https://api.github.com/repos/{repo}/contents/raw/{view}/{cls}?ref={ref}"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{ref}/raw/{view}/{cls}/{name}"
TARGET_SIZE = 1024
MASK_THRESHOLD = 10  # pixel intensity (sum across channels) above this = leaf

# 5 visually distinct classes used for the benchmark train/eval split.
BENCHMARK_CLASSES = [
    "Apple___Apple_scab",
    "Grape___Black_rot",
    "Tomato___Early_blight",
    "Potato___Late_blight",
    "Strawberry___Leaf_scorch",
]

# Full set of 38 PlantVillage classes (used with --full).
ALL_CLASSES = [
    "Apple___Apple_scab",
    "Apple___Black_rot",
    "Apple___Cedar_apple_rust",
    "Apple___healthy",
    "Blueberry___healthy",
    "Cherry_(including_sour)___Powdery_mildew",
    "Cherry_(including_sour)___healthy",
    "Corn_(maize)___Cercospora_leaf_spot Gray_leaf_spot",
    "Corn_(maize)___Common_rust_",
    "Corn_(maize)___Northern_Leaf_Blight",
    "Corn_(maize)___healthy",
    "Grape___Black_rot",
    "Grape___Esca_(Black_Measles)",
    "Grape___Leaf_blight_(Isariopsis_Leaf_Spot)",
    "Grape___healthy",
    "Orange___Haunglongbing_(Citrus_greening)",
    "Peach___Bacterial_spot",
    "Peach___healthy",
    "Pepper,_bell___Bacterial_spot",
    "Pepper,_bell___healthy",
    "Potato___Early_blight",
    "Potato___Late_blight",
    "Potato___healthy",
    "Raspberry___healthy",
    "Soybean___healthy",
    "Squash___Powdery_mildew",
    "Strawberry___Leaf_scorch",
    "Strawberry___healthy",
    "Tomato___Bacterial_spot",
    "Tomato___Early_blight",
    "Tomato___Late_blight",
    "Tomato___Leaf_Mold",
    "Tomato___Septoria_leaf_spot",
    "Tomato___Spider_mites Two-spotted_spider_mite",
    "Tomato___Target_Spot",
    "Tomato___Tomato_Yellow_Leaf_Curl_Virus",
    "Tomato___Tomato_mosaic_virus",
    "Tomato___healthy",
]


def _safe_class_dir(cls: str) -> str:
    """Class names contain commas/parens/spaces; use as-is in URL (urlencoded)."""
    import urllib.parse
    return urllib.parse.quote(cls, safe="")


def list_class_files(cls: str, view: str = "color") -> list[str]:
    """Return list of source filenames in a class folder. Single API call."""
    url = LIST_API.format(repo=GH_REPO, view=view, cls=_safe_class_dir(cls), ref=GH_REF)
    r = requests.get(url, timeout=30)
    if r.status_code == 403:
        sys.exit(
            f"GitHub API rate-limited (60/hr unauth). Wait an hour or set "
            f"GITHUB_TOKEN env var. URL: {url}"
        )
    r.raise_for_status()
    data = r.json()
    return sorted(
        f["name"] for f in data
        if f["type"] == "file" and f["name"].lower().endswith(".jpg")
    )


def fetch_blob(url: str, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            return r.content
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"failed after {retries} attempts: {url}") from last


def resize_square(img: Image.Image, size: int, mode: str = "image") -> Image.Image:
    """Resize a (likely square) PlantVillage image to size x size.

    PlantVillage native = 256x256 square. We upscale to 1024x1024 to match
    SAM2's native input. Bicubic for color images, nearest-neighbor for masks
    (preserves binary 0/255 values).
    """
    interp = Image.Resampling.BICUBIC if mode == "image" else Image.Resampling.NEAREST
    img = img.convert("RGB" if mode == "image" else "L")
    if img.size != (size, size):
        img = img.resize((size, size), interp)
    return ImageOps.exif_transpose(img)


def derive_mask(segmented_img: Image.Image, size: int) -> Image.Image:
    """Threshold the segmented PlantVillage view to a binary leaf mask.

    PlantVillage's segmented view places the leaf on a near-black background.
    Anything brighter than MASK_THRESHOLD across channels = leaf.
    """
    arr = np.array(segmented_img.convert("RGB"), dtype=np.int32)
    intensity = arr.sum(axis=2)
    mask = (intensity > MASK_THRESHOLD).astype(np.uint8) * 255
    pil = Image.fromarray(mask, mode="L")
    return resize_square(pil, size, mode="mask")


def process_one(
    cls: str,
    src_name: str,
    out_idx: int,
    out_images: Path,
    out_masks: Path,
) -> tuple[str, str, str] | None:
    """Download color + segmented for one image, save (image, mask). Thread-safe."""
    safe_cls = _safe_class_dir(cls)
    safe_color = _safe_class_dir(src_name)
    # Segmented view uses different naming: "<base>.JPG" -> "<base>_final_masked.jpg"
    base = src_name[:-4] if src_name.lower().endswith(".jpg") else src_name
    safe_seg = _safe_class_dir(f"{base}_final_masked.jpg")
    color_url = RAW_URL.format(repo=GH_REPO, ref=GH_REF, view="color", cls=safe_cls, name=safe_color)
    seg_url = RAW_URL.format(repo=GH_REPO, ref=GH_REF, view="segmented", cls=safe_cls, name=safe_seg)

    try:
        color_blob = fetch_blob(color_url)
        seg_blob = fetch_blob(seg_url)
        color_img = resize_square(Image.open(io.BytesIO(color_blob)), TARGET_SIZE, mode="image")
        mask_img = derive_mask(Image.open(io.BytesIO(seg_blob)), TARGET_SIZE)

        # Sanity: drop pairs where mask is empty or all-on (segmentation glitch)
        m = np.array(mask_img)
        frac = float((m > 0).sum()) / m.size
        if frac < 0.01 or frac > 0.99:
            return None

        out_name = f"{cls}__{out_idx:05d}.jpg"
        mask_name = f"{cls}__{out_idx:05d}.png"
        color_img.save(out_images / out_name, "JPEG", quality=92)
        mask_img.save(out_masks / mask_name, "PNG", optimize=True)
        return out_name, cls, src_name
    except Exception as e:  # noqa: BLE001
        print(f"  ! skip {cls}/{src_name}: {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20, help="images per class (ignored with --full)")
    parser.add_argument("--full", action="store_true", help="download all 38 classes, all images (~50k)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=24, help="parallel HTTP downloads")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "dataset")
    parser.add_argument("--train-frac", type=float, default=0.8)
    parser.add_argument("--eval-frac", type=float, default=0.2, help="of remaining 20%, this fraction is eval; rest is holdout")
    args = parser.parse_args()

    classes = ALL_CLASSES if args.full else BENCHMARK_CLASSES
    out_images = args.out / "images"
    out_masks = args.out / "masks"
    out_images.mkdir(parents=True, exist_ok=True)
    out_masks.mkdir(parents=True, exist_ok=True)

    # 1. List all source filenames per class (one API call each).
    print(f"listing {len(classes)} classes ...")
    per_class_files: dict[str, list[str]] = {}
    for cls in classes:
        files = list_class_files(cls, view="color")
        per_class_files[cls] = files
        print(f"  {cls}: {len(files)} files available")

    # 2. Pick up to --count per class (or all if --full).
    rng = np.random.default_rng(args.seed)
    picks: list[tuple[str, str, int]] = []  # (cls, src_name, output_idx)
    for cls, files in per_class_files.items():
        if args.full:
            chosen = files
        else:
            n = min(args.count, len(files))
            indices = rng.choice(len(files), size=n, replace=False)
            chosen = [files[int(i)] for i in sorted(indices)]
        for j, f in enumerate(chosen):
            picks.append((cls, f, j))

    total = len(picks)
    print(f"\ndownloading {total} (color, segmented) pairs at {args.workers} workers ...")
    t0 = time.time()
    rows: list[tuple[str, str, str]] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [
            ex.submit(process_one, cls, src, idx, out_images, out_masks)
            for cls, src, idx in picks
        ]
        for fut in as_completed(futures):
            res = fut.result()
            completed += 1
            if res:
                rows.append(res)
            if completed % 50 == 0 or completed == total:
                elapsed = time.time() - t0
                rate = completed / elapsed
                eta = (total - completed) / rate if rate > 0 else 0
                print(f"  {completed:>5}/{total}  ({rate:.1f}/s  ETA {eta:.0f}s)")

    # 3. Manifest + deterministic train/eval/holdout splits.
    rows.sort()  # deterministic order
    manifest_path = args.out / "manifest.csv"
    with manifest_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["filename", "class", "source_filename"])
        w.writerows(rows)

    rng2 = np.random.default_rng(args.seed)
    n = len(rows)
    perm = rng2.permutation(n)
    n_train = int(n * args.train_frac)
    n_remain = n - n_train
    n_eval = int(n_remain * args.eval_frac)
    train_files = sorted(rows[i][0] for i in perm[:n_train])
    eval_files = sorted(rows[i][0] for i in perm[n_train:n_train + n_eval])
    holdout_files = sorted(rows[i][0] for i in perm[n_train + n_eval:])

    splits = {
        "seed": args.seed,
        "n_total": n,
        "n_train": len(train_files),
        "n_eval": len(eval_files),
        "n_holdout": len(holdout_files),
        "train": train_files,
        "eval": eval_files,
        "holdout": holdout_files,
    }
    (args.out / "splits.json").write_text(json.dumps(splits, indent=2))

    (args.out / "LICENSE.txt").write_text(
        "PlantVillage dataset: CC0 1.0 (Public Domain).\n"
        f"Source: https://github.com/{GH_REPO} (ref: {GH_REF})\n"
        "Original publication: Hughes & Salathe, 2015, arXiv:1511.08060\n\n"
        "Mask derivation: thresholded the original `raw/segmented/` PlantVillage\n"
        "view (intensity > 10 across channels = leaf). No human labeling.\n"
        "Resize: PlantVillage native 256x256 -> 1024x1024 bicubic (image) / nearest (mask).\n"
    )

    elapsed = time.time() - t0
    print(f"\ndone in {elapsed:.0f}s. wrote {len(rows)} pairs to {args.out}")
    print(f"  images: {out_images}")
    print(f"  masks:  {out_masks}")
    print(f"  splits: {len(train_files)} train / {len(eval_files)} eval / {len(holdout_files)} holdout")
    print("\nnext: uv run python benchmarks/plantvillage/benchmark_train.py --config lora_r8")


if __name__ == "__main__":
    main()
