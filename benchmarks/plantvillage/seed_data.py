"""
Curate 100 PlantVillage leaf images for the SAM2 LoRA benchmark.

Pulls images directly from the spMohanty/PlantVillage-Dataset GitHub mirror
(public, CC0, no API key needed).

Output:
  benchmarks/plantvillage/seed/
    images/<class>__<idx>.jpg   (100 images, 1024x1024 letterboxed)
    manifest.csv                (filename, class, source_filename)
    LICENSE.txt                 (CC0 attribution)

Selection criteria (deliberate, documented for reproducibility):
  - 5 disease classes with visually distinct lesions on otherwise green leaves:
      Apple___Apple_scab
      Grape___Black_rot
      Tomato___Early_blight
      Potato___Late_blight
      Strawberry___Leaf_scorch
  - 20 images per class, picked uniformly across the alphabetical file listing
  - Resized to 1024x1024 with letterbox padding (SAM2 native input)
  - Reproducible: --seed flag fixes selection offset

Usage:
  uv pip install pillow requests
  uv run python benchmarks/plantvillage/seed_data.py --seed 42
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

import requests
from PIL import Image, ImageOps

CLASSES = [
    "Apple___Apple_scab",
    "Grape___Black_rot",
    "Tomato___Early_blight",
    "Potato___Late_blight",
    "Strawberry___Leaf_scorch",
]
PER_CLASS = 20
TARGET_SIZE = 1024
GH_REPO = "spMohanty/PlantVillage-Dataset"
GH_REF = "master"
LIST_API = "https://api.github.com/repos/{repo}/contents/raw/color/{cls}?ref={ref}"


def list_class_files(cls: str) -> list[dict]:
    url = LIST_API.format(repo=GH_REPO, cls=cls, ref=GH_REF)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return [f for f in r.json() if f["type"] == "file" and f["name"].lower().endswith(".jpg")]


def download(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def letterbox(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGB")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.paste(img, (x, y))
    return ImageOps.exif_transpose(canvas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "seed",
    )
    args = parser.parse_args()

    out_images = args.out / "images"
    out_images.mkdir(parents=True, exist_ok=True)

    manifest_path = args.out / "manifest.csv"
    with manifest_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["filename", "class", "source_filename"])

        for cls in CLASSES:
            print(f"\n[{cls}] listing files ...")
            files = sorted(list_class_files(cls), key=lambda f: f["name"])
            if len(files) < PER_CLASS:
                raise SystemExit(f"  only {len(files)} files in {cls}, need {PER_CLASS}")
            step = len(files) / PER_CLASS
            offset = args.seed % max(int(step), 1)
            picks = [files[min(int(i * step) + offset, len(files) - 1)] for i in range(PER_CLASS)]
            print(f"  {len(files)} total, picked {len(picks)} (step={step:.2f}, offset={offset})")

            for j, f in enumerate(picks):
                blob = download(f["download_url"])
                img = Image.open(io.BytesIO(blob))
                squared = letterbox(img, TARGET_SIZE)
                fname = f"{cls}__{j:02d}.jpg"
                squared.save(out_images / fname, "JPEG", quality=92)
                writer.writerow([fname, cls, f["name"]])
                print(f"  [{j + 1:>2}/{PER_CLASS}] {fname}")

    (args.out / "LICENSE.txt").write_text(
        "PlantVillage dataset: CC0 1.0 (Public Domain).\n"
        f"Source: https://github.com/{GH_REPO} (ref: {GH_REF})\n"
        "Original publication: Hughes & Salathe, 2015, arXiv:1511.08060\n\n"
        "These curated images are a subset selected for benchmarking SAM2 LoRA "
        "fine-tuning. Only transformation: letterbox resize to 1024x1024.\n"
    )

    total = sum(1 for _ in out_images.glob("*.jpg"))
    print(f"\ndone. wrote {total} images to {out_images}")
    print(f"manifest: {manifest_path}")
    print("\nnext: bulk-upload the images to your Anvil instance,")
    print("then label lesions per benchmarks/plantvillage/LABELING.md")


if __name__ == "__main__":
    main()
