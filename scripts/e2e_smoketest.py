#!/usr/bin/env python
"""End-to-end smoke test for Anvil API + ML.

Run with both services up:
  uvicorn anvil_ml.main:app --port 8001 &
  uvicorn anvil_api.main:app --port 8000 &
  python scripts/e2e_smoketest.py
"""
import sys
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000"
SAMPLE = Path(__file__).resolve().parent.parent / "tmp" / "cucumber.jpg"


def main() -> int:
    print(f"sample image: {SAMPLE}")
    assert SAMPLE.exists(), f"missing {SAMPLE}"

    c = httpx.Client(base_url=API, timeout=60.0)

    print("\n[1] login")
    r = c.post("/api/auth/login", json={"email": "admin@anvil.dev", "password": "admin"})
    r.raise_for_status()
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    print(f"  token: {token[:30]}...")

    print("\n[2] /me")
    r = c.get("/api/auth/me", headers=h)
    r.raise_for_status()
    print(f"  user: {r.json()}")

    print("\n[3] create project")
    r = c.post("/api/projects", headers=h, json={"name": "Cucumber Defects E2E", "description": "smoketest"})
    r.raise_for_status()
    project = r.json()
    print(f"  project: {project['id']} slug={project['slug']}")

    print("\n[4] create dataset")
    r = c.post(
        f"/api/projects/{project['id']}/datasets",
        headers=h, json={"name": "Batch 01"},
    )
    r.raise_for_status()
    dataset = r.json()
    print(f"  dataset: {dataset['id']}")

    print("\n[5] create class 'sunkenAreas'")
    r = c.post(
        f"/api/projects/{project['id']}/classes",
        headers=h, json={"name": "sunkenAreas"},
    )
    r.raise_for_status()
    cls = r.json()
    print(f"  class: {cls['id']} color={cls['color']}")

    print("\n[6] upload image")
    with open(SAMPLE, "rb") as f:
        r = c.post(
            f"/api/projects/{project['id']}/datasets/{dataset['id']}/images",
            headers=h, files={"files": ("cucumber.jpg", f, "image/jpeg")},
        )
    r.raise_for_status()
    images = r.json()
    img = images[0]
    print(f"  image: {img['id']} status={img['status']} {img['width']}x{img['height']}")

    print("\n[7] wait for embedding...")
    deadline = time.time() + 120
    while time.time() < deadline:
        r = c.get(f"/api/images/{img['id']}", headers=h)
        st = r.json()["status"]
        if st == "ready":
            print(f"  ready in {120 - (deadline - time.time()):.1f}s")
            break
        if st == "failed":
            print(f"  FAILED: {r.json().get('error')}")
            return 1
        time.sleep(1)
    else:
        print("  TIMEOUT waiting for embedding")
        return 1

    print("\n[8] predict (auto-annotate at center)")
    cx, cy = img["width"] // 2, img["height"] // 2
    r = c.post(
        f"/api/images/{img['id']}/predict",
        headers=h, json={"points": [[cx, cy]], "point_labels": [1]},
    )
    r.raise_for_status()
    pred = r.json()
    print(f"  score={pred['score']:.3f} polygon points={len(pred['polygon'])} bbox={pred['bbox']}")
    assert len(pred["polygon"]) > 3, "polygon too small"

    print("\n[9] save predicted polygon as annotation")
    r = c.post(
        f"/api/images/{img['id']}/annotations",
        headers=h,
        json={
            "class_id": cls["id"],
            "geometry_type": "polygon",
            "geometry": {"points": pred["polygon"]},
            "source": "model",
            "confidence": pred["score"],
        },
    )
    r.raise_for_status()
    ann = r.json()
    print(f"  annotation: {ann['id']}")

    print("\n[10] list annotations")
    r = c.get(f"/api/images/{img['id']}/annotations", headers=h)
    print(f"  total: {len(r.json())}")

    print("\nE2E OK ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
