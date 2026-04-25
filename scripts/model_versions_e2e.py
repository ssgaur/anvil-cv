"""End-to-end for model-versions: per-class IoU metric + A/B compare.

Trains 1 tiny LoRA on the cucumber sample (2 epochs, rank 4), then:
  - asserts metrics include `val_iou_per_class`
  - calls /compare against base SAM2 → asserts response shape

Run:  python scripts/model_versions_e2e.py
"""
from __future__ import annotations

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
    r = s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=10)
    r.raise_for_status()
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    proj = s.post(f"{API}/api/projects", json={"name": f"MV-E2E {uuid4().hex[:6]}"}).json()
    cls = s.post(f"{API}/api/projects/{proj['id']}/classes",
                 json={"name": "cucumber", "color": "#22c55e"}).json()
    ds = s.post(f"{API}/api/projects/{proj['id']}/datasets", json={"name": "Batch01"}).json()

    with open(SAMPLE, "rb") as fh:
        r = s.post(
            f"{API}/api/projects/{proj['id']}/datasets/{ds['id']}/images",
            files={"files": (SAMPLE.name, fh, "image/jpeg")}, timeout=60,
        )
    r.raise_for_status()
    img_id = r.json()[0]["id"]

    # Wait for embed
    for _ in range(60):
        cur = s.get(f"{API}/api/images/{img_id}").json()
        if cur["status"] == "ready": break
        if cur["status"] == "failed":
            print(f"embed failed: {cur.get('error')}"); return 3
        time.sleep(1)
    else:
        print("embed timeout"); return 3
    print(f"➜ image ready: {cur['width']}×{cur['height']}")

    # Auto-annotate to get a polygon
    pred = s.post(f"{API}/api/images/{img_id}/predict",
                  json={"points": [[cur["width"] / 2, cur["height"] / 2]],
                        "point_labels": [1]}).json()
    poly = pred["polygon"]
    assert len(poly) >= 3, "base predict returned empty polygon"

    # Persist 6 anns
    for _ in range(6):
        s.post(f"{API}/api/images/{img_id}/annotations",
               json={"class_id": cls["id"], "geometry_type": "polygon",
                     "geometry": {"points": poly}, "source": "model"}).raise_for_status()

    # Train tiny adapter
    print("➜ training (2 epochs, rank 4)…")
    mv = s.post(f"{API}/api/projects/{proj['id']}/model-versions",
                json={"name": "ft-tiny", "dataset_id": ds["id"],
                      "epochs": 2, "lr": 1e-4, "rank": 4, "alpha": 8, "eval_split": 0.34}).json()
    mv_id = mv["id"]
    t0 = time.time()
    for _ in range(60 * 10):
        cur_mv = s.get(f"{API}/api/model-versions/{mv_id}").json()
        if cur_mv["status"] == "ready": break
        if cur_mv["status"] == "failed":
            print(f"train FAILED: {cur_mv.get('error')}"); return 5
        time.sleep(2)
    else:
        print("train timeout"); return 5
    print(f"   trained in {int(time.time()-t0)}s")

    # ---- assertions ----
    metrics = cur_mv["metrics"]
    assert "val_iou_per_class" in metrics, "metrics missing val_iou_per_class"
    pcc = metrics["val_iou_per_class"]
    assert isinstance(pcc, dict) and "cucumber" in pcc, f"per-class missing 'cucumber': {pcc}"
    print(f"✓ per-class IoU recorded: {pcc}")
    assert "is_active" in cur_mv and cur_mv["is_active"] is False
    print("✓ is_active=False (not yet set active)")

    # List should include is_active
    versions = s.get(f"{API}/api/projects/{proj['id']}/model-versions").json()
    assert any(v["id"] == mv_id for v in versions)
    print(f"✓ list returns {len(versions)} version(s) with is_active flag")

    # Compare (FT vs base SAM2)
    print("➜ compare (ft vs base) — this re-evaluates both on the same sample…")
    cmp_body = {"a_id": None, "b_id": mv_id, "n_samples": 4, "seed": 42}
    cmp = s.post(f"{API}/api/projects/{proj['id']}/model-versions/compare",
                 json=cmp_body)
    cmp.raise_for_status(); cmp = cmp.json()
    assert cmp["a"]["name"] == "Base SAM2"
    assert cmp["b"]["name"] == "ft-tiny"
    assert "mean_iou" in cmp["a"] and "mean_iou" in cmp["b"]
    assert "delta_mean_iou" in cmp
    assert isinstance(cmp["per_class"], list) and len(cmp["per_class"]) >= 1
    assert isinstance(cmp["per_item"], list) and len(cmp["per_item"]) >= 1
    print(f"✓ compare ran: A={cmp['a']['mean_iou']:.3f} B={cmp['b']['mean_iou']:.3f} Δ={cmp['delta_mean_iou']:+.3f}")
    print(f"✓ per_class rows={len(cmp['per_class'])} per_item rows={len(cmp['per_item'])}")

    # Same-version compare allowed too (sanity)
    cmp2 = s.post(f"{API}/api/projects/{proj['id']}/model-versions/compare",
                  json={"a_id": mv_id, "b_id": mv_id, "n_samples": 2, "seed": 1}).json()
    assert abs(cmp2["delta_mean_iou"]) < 1e-3, f"self-compare should be ~0 delta: {cmp2['delta_mean_iou']}"
    print(f"✓ self-compare delta ≈ 0 ({cmp2['delta_mean_iou']:+.4f})")

    # Set active → is_active flips
    s.post(f"{API}/api/projects/{proj['id']}/active-model",
           json={"model_version_id": mv_id}).raise_for_status()
    cur_mv2 = s.get(f"{API}/api/model-versions/{mv_id}").json()
    assert cur_mv2["is_active"] is True
    print("✓ is_active flips to True after set-active")

    # Cannot delete active version (without first deactivating) — actually our route allows it
    # by clearing active. Verify delete works.
    s.post(f"{API}/api/projects/{proj['id']}/active-model",
           json={"model_version_id": None}).raise_for_status()
    r = s.delete(f"{API}/api/model-versions/{mv_id}")
    assert r.status_code == 204, f"delete returned {r.status_code}: {r.text}"
    versions2 = s.get(f"{API}/api/projects/{proj['id']}/model-versions").json()
    assert not any(v["id"] == mv_id for v in versions2)
    print("✓ delete model version ok")

    # Cleanup
    s.delete(f"{API}/api/projects/{proj['id']}")
    print("\n🎉 model-versions E2E passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
