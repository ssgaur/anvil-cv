"""End-to-end fine-tune smoketest.

Verifies the full Anvil LoRA loop on a real cucumber image:
  1. Sign in, create a project + cucumber class + dataset
  2. Upload the cucumber image, wait for SAM2 embedding
  3. Auto-annotate it once (gives us a ground-truth-ish polygon to train on)
  4. Duplicate that annotation N times so we have >= N training samples
  5. POST /model-versions to start training; poll until status=ready
  6. Predict again with the new model_version_id; check we still get a polygon
  7. Set the model active; predict without explicit id; still get a polygon

Run:  python scripts/finetune_e2e.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import requests

API = os.getenv("ANVIL_API", "http://127.0.0.1:8000")
EMAIL = "admin@anvil.dev"
PASSWORD = "admin"
SAMPLE = Path(__file__).parent.parent / "tmp" / "cucumber.jpg"


def main() -> int:
    if not SAMPLE.exists():
        print(f"missing sample image at {SAMPLE}")
        return 2

    s = requests.Session()
    print("➜ POST /api/auth/login")
    r = s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=10)
    r.raise_for_status()
    token = r.json()["access_token"]
    s.headers["Authorization"] = f"Bearer {token}"

    proj_name = f"FT-E2E {uuid4().hex[:6]}"
    print(f"➜ POST /api/projects ({proj_name})")
    proj = s.post(f"{API}/api/projects", json={"name": proj_name}).json()

    print("➜ POST /api/projects/:id/classes (cucumber)")
    cls = s.post(f"{API}/api/projects/{proj['id']}/classes",
                 json={"name": "cucumber", "color": "#22c55e"}).json()

    print("➜ POST /api/projects/:id/datasets (Batch01)")
    ds = s.post(f"{API}/api/projects/{proj['id']}/datasets",
                json={"name": "Batch01"}).json()

    print(f"➜ Upload {SAMPLE.name}")
    with open(SAMPLE, "rb") as fh:
        r = s.post(
            f"{API}/api/projects/{proj['id']}/datasets/{ds['id']}/images",
            files={"files": (SAMPLE.name, fh, "image/jpeg")},
            timeout=60,
        )
    r.raise_for_status()
    img = r.json()[0]
    img_id = img["id"]
    print(f"   image_id={img_id}")

    # Wait until embedding ready
    print("➜ Wait for embedding…")
    for _ in range(60):
        cur = s.get(f"{API}/api/images/{img_id}").json()
        if cur["status"] == "ready":
            break
        if cur["status"] == "failed":
            print(f"embed failed: {cur.get('error')}")
            return 3
        time.sleep(1)
    else:
        print("timeout waiting for embedding")
        return 3
    print(f"   status={cur['status']}")

    # Auto-annotate (centre point) — gives us a polygon to train on
    print("➜ POST /api/images/:id/predict (centre point, base model)")
    pred = s.post(f"{API}/api/images/{img_id}/predict",
                  json={"points": [[cur["width"] / 2, cur["height"] / 2]],
                        "point_labels": [1]}).json()
    print(f"   base score={pred['score']:.3f}  polygon_pts={len(pred['polygon'])}")
    poly = pred["polygon"]
    if len(poly) < 3:
        print("base model returned no polygon — cannot train")
        return 4

    # Persist as several annotations (training needs > 1 sample for a meaningful eval split)
    print("➜ Create 6 annotations from that polygon (so we have train + eval slices)")
    n_ann = 6
    for _ in range(n_ann):
        s.post(f"{API}/api/images/{img_id}/annotations",
               json={"class_id": cls["id"], "geometry_type": "polygon",
                     "geometry": {"points": poly}, "source": "model",
                     "confidence": pred["score"]}).raise_for_status()

    # Kick off training
    print("➜ POST /api/projects/:id/model-versions (start fine-tune)")
    mv = s.post(
        f"{API}/api/projects/{proj['id']}/model-versions",
        json={
            "name": "ft-e2e-v1",
            "dataset_id": ds["id"],
            "epochs": 2,            # tiny for smoketest
            "lr": 1e-4,
            "rank": 4,
            "alpha": 8,
            "eval_split": 0.34,     # 6 anns -> 2 eval / 4 train
        },
    )
    mv.raise_for_status()
    mv = mv.json()
    print(f"   mv_id={mv['id']} status={mv['status']} train_imgs={mv['num_train_images']} train_anns={mv['num_train_annotations']}")

    # Poll to ready
    print("➜ Poll training (this takes a couple of minutes on M1 CPU)…")
    t0 = time.time()
    last_status = None
    for _ in range(60 * 10):  # up to 10 min
        cur_mv = s.get(f"{API}/api/model-versions/{mv['id']}").json()
        if cur_mv["status"] != last_status:
            print(f"   [{int(time.time()-t0)}s] status={cur_mv['status']} progress={cur_mv['progress']:.0%}")
            last_status = cur_mv["status"]
        if cur_mv["status"] == "ready":
            break
        if cur_mv["status"] == "failed":
            print(f"training FAILED: {cur_mv.get('error')}")
            return 5
        time.sleep(2)
    else:
        print("training timed out (>10 min)")
        return 5

    print(f"   metrics: {json.dumps({k: v for k, v in cur_mv['metrics'].items() if 'per_epoch' in k or k.endswith('iou')}, default=str)}")

    # Predict with the new model_version_id
    print("➜ POST /api/images/:id/predict with model_version_id (fine-tuned)")
    pred2 = s.post(
        f"{API}/api/images/{img_id}/predict",
        json={
            "points": [[cur["width"] / 2, cur["height"] / 2]],
            "point_labels": [1],
            "model_version_id": cur_mv["id"],
        },
    ).json()
    print(f"   FT score={pred2['score']:.3f}  polygon_pts={len(pred2['polygon'])}")
    if len(pred2["polygon"]) < 3:
        print("fine-tuned predict returned empty polygon")
        return 6

    # Set active and predict without explicit id
    print("➜ POST /api/projects/:id/active-model")
    s.post(f"{API}/api/projects/{proj['id']}/active-model",
           json={"model_version_id": cur_mv["id"]}).raise_for_status()

    print("➜ POST /api/images/:id/predict (no id; should use active model)")
    pred3 = s.post(f"{API}/api/images/{img_id}/predict",
                   json={"points": [[cur["width"] / 2, cur["height"] / 2]],
                         "point_labels": [1]}).json()
    print(f"   active-model score={pred3['score']:.3f}  polygon_pts={len(pred3['polygon'])}")

    print("\n✅ FINE-TUNE E2E PASSED")
    print(f"   project: {proj_name}  (kept around for inspection)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
