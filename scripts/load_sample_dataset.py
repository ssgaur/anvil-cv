#!/usr/bin/env python3
"""Load a small synthetic demo dataset into a running Anvil instance.

No external Python dependencies are required. The script generates PNG images
with the standard library, logs in as the default dev admin, creates a project,
dataset, classes, uploads the images, and adds a few starter annotations.

Run from the repo root after `docker compose up -d --build`:

    python3 scripts/load_sample_dataset.py

Environment:
    ANVIL_API=http://localhost:8000
    ANVIL_ADMIN_EMAIL=admin@anvil.dev
    ANVIL_ADMIN_PASSWORD=admin
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import struct
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path
from typing import Any

API = os.environ.get("ANVIL_API", "http://localhost:8000").rstrip("/")
ADMIN_EMAIL = os.environ.get("ANVIL_ADMIN_EMAIL", "admin@anvil.dev")
ADMIN_PASSWORD = os.environ.get("ANVIL_ADMIN_PASSWORD", "admin")
PROJECT_NAME = "Anvil demo - leaves"
DATASET_NAME = "sample leaves"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def png_bytes(width: int, height: int, pixels: list[tuple[int, int, int]]) -> bytes:
    """Encode RGB pixels as PNG using only stdlib zlib/struct."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        row = pixels[y * width:(y + 1) * width]
        for r, g, b in row:
            raw.extend((r, g, b))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def make_leaf_image(seed: int, size: int = 256) -> bytes:
    rng = random.Random(seed)
    bg = (18 + rng.randint(0, 12), 25 + rng.randint(0, 10), 22 + rng.randint(0, 10))
    leaf = (42 + rng.randint(0, 35), 130 + rng.randint(0, 70), 65 + rng.randint(0, 35))
    vein = (170 + rng.randint(0, 30), 215 + rng.randint(0, 25), 140 + rng.randint(0, 30))
    disease = (220 + rng.randint(0, 20), 145 + rng.randint(0, 40), 55 + rng.randint(0, 35))

    cx = size / 2 + rng.randint(-18, 18)
    cy = size / 2 + rng.randint(-12, 18)
    rx = 82 + rng.randint(-12, 14)
    ry = 116 + rng.randint(-15, 10)
    angle = math.radians(rng.randint(-28, 28))
    ca, sa = math.cos(angle), math.sin(angle)

    pix = [bg for _ in range(size * size)]

    def setpx(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < size and 0 <= y < size:
            pix[y * size + x] = color

    # Leaf body: rotated ellipse with pointed ends.
    for y in range(size):
        for x in range(size):
            dx, dy = x - cx, y - cy
            u = (dx * ca + dy * sa) / rx
            v = (-dx * sa + dy * ca) / ry
            taper = 1.0 - 0.35 * abs(v)
            if (u / max(taper, 0.15)) ** 2 + v ** 2 <= 1.0:
                shade = int(18 * (1 - abs(u)) + rng.randint(-3, 3))
                setpx(x, y, (
                    max(0, min(255, leaf[0] + shade)),
                    max(0, min(255, leaf[1] + shade)),
                    max(0, min(255, leaf[2] + shade)),
                ))

    # Main vein and smaller veins.
    for t in [i / 240 for i in range(-115, 116)]:
        x = int(cx + (t * ry) * sa)
        y = int(cy + (t * ry) * ca)
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                setpx(x + dx, y + dy, vein)
        if int((t + 1) * 50) % 13 == 0:
            for side in (-1, 1):
                for k in range(28):
                    xx = int(x + side * k * ca + k * 0.25 * sa)
                    yy = int(y - side * k * sa + k * 0.25 * ca)
                    setpx(xx, yy, vein)

    # Disease spots / fruit-like OBB target.
    for _ in range(4 + seed % 4):
        sx = cx + rng.randint(-45, 45)
        sy = cy + rng.randint(-70, 70)
        sr = rng.randint(7, 16)
        for y in range(int(sy - sr), int(sy + sr) + 1):
            for x in range(int(sx - sr), int(sx + sr) + 1):
                if (x - sx) ** 2 + (y - sy) ** 2 <= sr ** 2:
                    setpx(x, y, disease)

    return png_bytes(size, size, pix)


def http_json(method: str, path: str, token: str | None = None, body: Any | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as e:
        fail(f"{method} {path} -> {e.code}: {e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        fail(f"{method} {path} failed: {e}")


def http_upload(path: str, token: str, file_path: Path) -> Any:
    boundary = f"----anvil-{uuid.uuid4().hex}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            f'Content-Disposition: form-data; name="files"; filename="{file_path.name}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
    )
    body.extend(file_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    req = urllib.request.Request(f"{API}{path}", data=bytes(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        fail(f"POST {path} upload -> {e.code}: {e.read().decode('utf-8', 'replace')}")


def by_name(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((r for r in rows if r.get("name") == name), None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=PROJECT_NAME)
    ap.add_argument("--dataset", default=DATASET_NAME)
    ap.add_argument("--count", type=int, default=8, help="number of generated images")
    args = ap.parse_args()

    print(f"Connecting to {API} as {ADMIN_EMAIL} ...")
    login = http_json("POST", "/api/auth/login", body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    token = login.get("access_token")
    if not token:
        fail("login response did not contain access_token")

    projects = http_json("GET", "/api/projects", token=token)
    project = by_name(projects, args.project)
    if project is None:
        project = http_json("POST", "/api/projects", token=token, body={
            "name": args.project,
            "description": "Synthetic launch demo dataset generated by scripts/load_sample_dataset.py",
        })
        print(f"Created project: {project['name']}")
    else:
        print(f"Reusing project: {project['name']}")

    pid = project["id"]
    datasets = http_json("GET", f"/api/projects/{pid}/datasets", token=token)
    dataset = by_name(datasets, args.dataset)
    if dataset is None:
        dataset = http_json("POST", f"/api/projects/{pid}/datasets", token=token, body={"name": args.dataset})
        print(f"Created dataset: {dataset['name']}")
    else:
        print(f"Reusing dataset: {dataset['name']}")
    did = dataset["id"]

    classes = http_json("GET", f"/api/projects/{pid}/classes", token=token)

    def ensure_class(name: str, color: str, geometry_type: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        row = by_name(classes, name)
        if row:
            return row
        body: dict[str, Any] = {"name": name, "color": color, "geometry_type": geometry_type}
        if extra:
            body.update(extra)
        row = http_json("POST", f"/api/projects/{pid}/classes", token=token, body=body)
        classes.append(row)
        print(f"Created class: {name} ({geometry_type})")
        return row

    leaf_cls = ensure_class("leaf", "#22c55e", "polygon")
    stem_cls = ensure_class("stem", "#84cc16", "polyline")
    spot_cls = ensure_class("disease spot", "#f59e0b", "obb")
    landmark_cls = ensure_class("leaf landmarks", "#06b6d4", "keypoints", {
        "keypoint_schema": {
            "keypoints": [{"name": "tip"}, {"name": "base"}],
            "edges": [[0, 1]],
        }
    })

    existing_images = http_json("GET", f"/api/projects/{pid}/datasets/{did}/images", token=token)
    existing_names = {i.get("filename") for i in existing_images}

    uploaded = []
    with tempfile.TemporaryDirectory(prefix="anvil-demo-") as td:
        root = Path(td)
        for i in range(max(1, args.count)):
            name = f"demo_leaf_{i + 1:02d}.png"
            if name in existing_names:
                print(f"Skipping existing image: {name}")
                continue
            p = root / name
            p.write_bytes(make_leaf_image(1000 + i))
            rows = http_upload(f"/api/projects/{pid}/datasets/{did}/images", token, p)
            img = rows[0] if isinstance(rows, list) else rows
            uploaded.append(img)
            print(f"Uploaded: {name}")

    # Add starter annotations to newly uploaded images. Coordinates match the
    # synthetic 256x256 images closely enough for demo screenshots.
    for img in uploaded:
        img_id = img["id"]
        anns = [
            {
                "class_id": leaf_cls["id"],
                "geometry_type": "polygon",
                "geometry": {"points": [[128, 18], [197, 62], [214, 145], [160, 235], [75, 219], [39, 136], [58, 58]]},
            },
            {
                "class_id": stem_cls["id"],
                "geometry_type": "polyline",
                "geometry": {"points": [[128, 235], [126, 185], [128, 130], [130, 72], [128, 20]]},
            },
            {
                "class_id": spot_cls["id"],
                "geometry_type": "obb",
                "geometry": {"cx": 157, "cy": 116, "w": 42, "h": 24, "angle_rad": 0.35},
            },
            {
                "class_id": landmark_cls["id"],
                "geometry_type": "keypoints",
                "geometry": {"points": [[128, 20, 2], [128, 235, 2]]},
            },
        ]
        for ann in anns:
            http_json("POST", f"/api/images/{img_id}/annotations", token=token, body=ann)
        print(f"Annotated: {img['filename']}")

    print("")
    print("Done.")
    print(f"Project: {args.project}")
    print(f"Dataset: {args.dataset}")
    print(f"Open: http://localhost:5173/projects/{pid}/datasets/{did}")
    print("Tip: open an image, press '?' for shortcuts, then try A (SAM2), P, L, O, K.")


if __name__ == "__main__":
    main()

