"""Try SAM3 concept segmentation against a real image in your local stack.

SAM3 is wired into the ML service but not yet into the Workview UI, so this is
the quickest way to see it work end to end. It resolves a real uploaded image,
asks SAM3 for every instance of a text concept, prints the scores, and can
write an overlay PNG so you can eyeball the masks.

Examples:
    python3 scripts/sam3_try.py --text leaf
    python3 scripts/sam3_try.py --text "leaf" --threshold 0.3 --out /tmp/out.png
    python3 scripts/sam3_try.py --list
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

import requests

API = os.getenv("ANVIL_API", "http://127.0.0.1:8000")
ML = os.getenv("ANVIL_ML", "http://127.0.0.1:8001")
EMAIL = os.getenv("ANVIL_ADMIN_EMAIL", "admin@anvil.dev")
PASSWORD = os.getenv("ANVIL_ADMIN_PASSWORD", "admin")

# Must match the key built in routes/images.py::upload_images, since the ML
# container reads the same shared volume at /data.
STORAGE_ROOT = os.getenv("ANVIL_ML_STORAGE_ROOT", "/data")


def login(s: requests.Session) -> None:
    r = s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if r.status_code != 200:
        sys.exit(f"login failed ({r.status_code}). Is the API up? {r.text[:200]}")
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


# Meta's own SAM3 test photos. The generated sample dataset is synthetic
# shapes, which SAM3 correctly does not recognise as real objects — so a
# meaningful first test needs real imagery.
DEMO_PROJECT = "SAM3 demo"
DEMO_DATASET = "real photos"
DEMO_IMAGES = {
    "truck.jpg": "https://raw.githubusercontent.com/facebookresearch/sam3/96914d2425f90a64f45ca977c2b5165418099543/assets/images/truck.jpg",
    "groceries.jpg": "https://raw.githubusercontent.com/facebookresearch/sam3/96914d2425f90a64f45ca977c2b5165418099543/assets/images/groceries.jpg",
}


def ensure_demo(s: requests.Session) -> tuple[dict, dict]:
    """Create (idempotently) a project holding real photos for SAM3 testing."""
    projects = s.get(f"{API}/api/projects").json()
    proj = next((p for p in projects if p["name"] == DEMO_PROJECT), None)
    if proj is None:
        proj = s.post(f"{API}/api/projects", json={"name": DEMO_PROJECT}).json()
        print(f"created project: {DEMO_PROJECT}")

    datasets = s.get(f"{API}/api/projects/{proj['id']}/datasets").json()
    ds = next((d for d in datasets if d["name"] == DEMO_DATASET), None)
    if ds is None:
        ds = s.post(
            f"{API}/api/projects/{proj['id']}/datasets", json={"name": DEMO_DATASET}
        ).json()
        print(f"created dataset: {DEMO_DATASET}")

    existing = {
        i["filename"]
        for i in s.get(
            f"{API}/api/projects/{proj['id']}/datasets/{ds['id']}/images"
        ).json()
    }
    todo = {k: v for k, v in DEMO_IMAGES.items() if k not in existing}
    if todo:
        files = []
        for name, url in todo.items():
            print(f"downloading {name} ...")
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            files.append(("files", (name, resp.content, "image/jpeg")))
        up = s.post(
            f"{API}/api/projects/{proj['id']}/datasets/{ds['id']}/images", files=files
        )
        up.raise_for_status()
        print(f"uploaded {len(todo)} image(s)")
    return proj, ds


def pick_image(s: requests.Session, dataset_id: str | None, index: int):
    """Return (project_id, dataset_id, image dict) for the first dataset with images."""
    projects = s.get(f"{API}/api/projects").json()
    for p in projects:
        datasets = s.get(f"{API}/api/projects/{p['id']}/datasets").json()
        for d in datasets:
            if dataset_id and d["id"] != dataset_id:
                continue
            imgs = s.get(
                f"{API}/api/projects/{p['id']}/datasets/{d['id']}/images"
            ).json()
            if imgs:
                if index >= len(imgs):
                    sys.exit(f"--index {index} out of range ({len(imgs)} images)")
                return p, d, imgs[index]
    sys.exit("no images found. Run: python3 scripts/load_sample_dataset.py")


def container_path(dataset_id: str, image: dict) -> str:
    ext = Path(image["filename"]).suffix.lower() or ".jpg"
    return f"{STORAGE_ROOT}/datasets/{dataset_id}/images/{image['id']}{ext}"


def overlay(s: requests.Session, image: dict, instances: list, out_path: str) -> None:
    from PIL import Image as PILImage
    from PIL import ImageDraw

    url = image.get("image_url") or ""
    if url.startswith("/"):
        url = f"{API}{url}"
    raw = s.get(url).content
    im = PILImage.open(io.BytesIO(raw)).convert("RGBA")

    layer = PILImage.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    palette = [
        (34, 197, 94), (59, 130, 246), (244, 63, 94),
        (234, 179, 8), (168, 85, 247), (20, 184, 166),
    ]
    for i, inst in enumerate(instances):
        colour = palette[i % len(palette)]
        pts = [(float(x), float(y)) for x, y in inst["polygon"]]
        if len(pts) >= 3:
            d.polygon(pts, fill=(*colour, 70), outline=(*colour, 255))
        x, y, w, h = inst["bbox"]
        d.rectangle([x, y, x + w, y + h], outline=(*colour, 255), width=3)
        d.text((x + 4, max(0, y - 14)), f"{inst['score']:.2f}", fill=(*colour, 255))

    PILImage.alpha_composite(im, layer).convert("RGB").save(out_path)
    print(f"\noverlay written: {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", default="leaf", help="concept to search for")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--dataset", default=None, help="dataset id (default: first found)")
    ap.add_argument("--index", type=int, default=0, help="which image in the dataset")
    ap.add_argument("--out", default=None, help="write an overlay PNG here")
    ap.add_argument("--list", action="store_true", help="list projects/datasets and exit")
    ap.add_argument(
        "--demo",
        action="store_true",
        help="use real demo photos (downloads them once); best for a first test",
    )
    args = ap.parse_args()

    s = requests.Session()
    login(s)

    if args.list:
        for p in s.get(f"{API}/api/projects").json():
            print(f"project {p['id']}  {p['name']}")
            for d in s.get(f"{API}/api/projects/{p['id']}/datasets").json():
                imgs = s.get(
                    f"{API}/api/projects/{p['id']}/datasets/{d['id']}/images"
                ).json()
                print(f"  dataset {d['id']}  {d['name']}  ({len(imgs)} images)")
        return 0

    if args.demo:
        proj, ds = ensure_demo(s)
        imgs = s.get(
            f"{API}/api/projects/{proj['id']}/datasets/{ds['id']}/images"
        ).json()
        if args.index >= len(imgs):
            sys.exit(f"--index {args.index} out of range ({len(imgs)} images)")
        img = imgs[args.index]
    else:
        proj, ds, img = pick_image(s, args.dataset, args.index)
    path = container_path(ds["id"], img)
    print(f"project : {proj['name']}")
    print(f"dataset : {ds['name']}")
    print(f"image   : {img['filename']}  ({img['width']}x{img['height']})")
    print(f"ml path : {path}")
    print(f"concept : {args.text!r}  threshold={args.threshold}\n")

    r = requests.post(
        f"{ML}/predict_concept",
        json={
            "image_path": path,
            "text": args.text,
            "threshold": args.threshold,
        },
        timeout=600,
    )
    if r.status_code != 200:
        sys.exit(f"SAM3 request failed ({r.status_code}): {r.text[:400]}")

    data = r.json()
    n = data["count"]
    print(f"SAM3 returned {n} instance(s)")
    for i, inst in enumerate(data["instances"]):
        bbox = [round(v) for v in inst["bbox"]]
        rle = "yes" if inst.get("mask_rle") else "no"
        print(
            f"  [{i}] score={inst['score']:.3f} bbox={bbox} "
            f"polygon_pts={len(inst['polygon'])} mask_rle={rle}"
        )
    if n == 0:
        print(
            "\n0 instances is a valid answer — it means SAM3 did not find this\n"
            "concept above the threshold. Try --threshold 0.2, or a concept that\n"
            "is actually in the image."
        )

    if args.out and n:
        overlay(s, img, data["instances"], args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
