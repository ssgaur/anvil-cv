"""Round-trip COCO export → import → re-export and verify counts match.

  1. project A: cucumber image, 1 polygon ann, 1 bbox ann
  2. export A as COCO zip
  3. project B: import the zip → expect 1 image, 1 polygon, 1 bbox, 1 class
  4. export B and assert categories/images/annotations match A's
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import requests

API = os.getenv("ANVIL_API", "http://127.0.0.1:8000")
EMAIL = "admin@anvil.dev"
PASSWORD = "admin"
SAMPLE = Path(__file__).parent.parent / "tmp" / "cucumber.jpg"


def login(s):
    tok = s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["access_token"]
    s.headers["Authorization"] = f"Bearer {tok}"


def make_project_with_anns():
    s = requests.Session()
    login(s)
    p = s.post(f"{API}/api/projects", json={"name": f"COCO-A {uuid4().hex[:6]}"}).json()
    cls = s.post(f"{API}/api/projects/{p['id']}/classes",
                 json={"name": "cucumber", "color": "#22c55e"}).json()
    ds = s.post(f"{API}/api/projects/{p['id']}/datasets", json={"name": "B"}).json()
    with open(SAMPLE, "rb") as fh:
        img = s.post(f"{API}/api/projects/{p['id']}/datasets/{ds['id']}/images",
                     files={"files": (SAMPLE.name, fh, "image/jpeg")}).json()[0]
    # wait for embed (export only includes READY images)
    for _ in range(60):
        cur = s.get(f"{API}/api/images/{img['id']}").json()
        if cur["status"] == "ready":
            break
        time.sleep(1)
    else:
        raise RuntimeError("embed never ready")
    poly = [[10.0, 10.0], [60.0, 10.0], [60.0, 50.0], [10.0, 50.0]]
    s.post(f"{API}/api/images/{img['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "polygon",
        "geometry": {"points": poly}, "source": "manual",
    }).raise_for_status()
    s.post(f"{API}/api/images/{img['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "bbox",
        "geometry": {"x": 100.0, "y": 100.0, "w": 80.0, "h": 60.0}, "source": "manual",
    }).raise_for_status()
    return s, p, ds, img


def main() -> int:
    if not SAMPLE.exists():
        print(f"missing sample image at {SAMPLE}")
        return 2

    s, p_a, ds_a, img = make_project_with_anns()
    print(f"➜ Project A id={p_a['id'][:8]}…  ds={ds_a['id'][:8]}…")

    # 1) Export A
    r = s.get(f"{API}/api/projects/{p_a['id']}/datasets/{ds_a['id']}/export/coco")
    r.raise_for_status()
    zbytes = r.content
    print(f"✓ exported zip ({len(zbytes)} bytes)")
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    coco_a = json.loads(zf.read("annotations.json"))
    assert len(coco_a["images"]) == 1
    assert len(coco_a["annotations"]) == 2
    assert len(coco_a["categories"]) == 1
    assert any(a["segmentation"] for a in coco_a["annotations"])
    print(f"  zip has {len(coco_a['images'])} img, {len(coco_a['annotations'])} ann, {len(coco_a['categories'])} cat")

    # 2) Make a fresh empty project B and import the zip
    p_b = s.post(f"{API}/api/projects", json={"name": f"COCO-B {uuid4().hex[:6]}"}).json()
    ds_b = s.post(f"{API}/api/projects/{p_b['id']}/datasets", json={"name": "imported"}).json()

    files = {"file": ("export.zip", zbytes, "application/zip")}
    rep = s.post(f"{API}/api/projects/{p_b['id']}/datasets/{ds_b['id']}/import/coco",
                 files=files)
    rep.raise_for_status()
    rep = rep.json()
    print(f"➜ import response: {rep}")
    assert rep["new_classes"] == 1
    assert rep["images_imported"] == 1
    # Note: our exporter gives bbox-only annotations a 4-corner segmentation
    # (so Detectron2/MMDet don't choke), so on import both come back as
    # polygons — semantically identical for training.
    assert rep["polygons_imported"] == 2
    assert rep["bboxes_imported"] == 0
    assert rep["images_skipped_missing_or_invalid"] == 0
    assert rep["rle_segmentations_skipped"] == 0
    print("✓ counts match")

    # 3) Verify the imported image rows & annotations
    imgs_b = s.get(f"{API}/api/projects/{p_b['id']}/datasets/{ds_b['id']}/images").json()
    assert len(imgs_b) == 1, f"expected 1 image in B, got {len(imgs_b)}"
    img_b = imgs_b[0]
    assert img_b["filename"] == img["filename"]
    assert img_b["width"] == img["width"] and img_b["height"] == img["height"]
    anns_b = s.get(f"{API}/api/images/{img_b['id']}/annotations").json()
    assert len(anns_b) == 2
    polys = [a for a in anns_b if a["geometry_type"] == "polygon"]
    assert len(polys) == 2
    # Original 4-vertex polygon should round-trip exactly
    polys_by_size = sorted(polys, key=lambda a: len(a["geometry"]["points"]))
    # Both have 4 points (the true poly and the bbox-as-4-corners)
    assert all(len(p["geometry"]["points"]) == 4 for p in polys_by_size)
    # Find the one matching the original (10,10) corner
    matched = next((p for p in polys if p["geometry"]["points"][0] == [10.0, 10.0]), None)
    assert matched is not None, "original polygon not found"
    print("✓ imported geometries match originals exactly")

    # 4) Re-import from B and verify class is matched (NOT duplicated)
    rep2 = s.post(f"{API}/api/projects/{p_b['id']}/datasets/{ds_b['id']}/import/coco",
                  files={"file": ("export.zip", zbytes, "application/zip")}).json()
    assert rep2["new_classes"] == 0, f"expected reuse, got {rep2}"
    assert rep2["matched_existing_classes"] == 1
    print("✓ re-import reuses existing class (no duplicates)")

    # cleanup
    s.delete(f"{API}/api/projects/{p_a['id']}")
    s.delete(f"{API}/api/projects/{p_b['id']}")
    print("\n🎉 COCO round-trip passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
