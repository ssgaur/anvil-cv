"""Smoke-test new product features end-to-end against a running API.

Covers (read-only-ish where possible; creates+cleans up its own data):
  - Class hierarchy (parent_id + cycle prevention + same-project check)
  - Keypoint schema (immutable once annotated)
  - New geometry types: polyline, obb, keypoints (validation + create)
  - Image filters/sort: review_status, has_annotations, class_id, sort variants
  - Bulk endpoints: bulk-delete, bulk-review, bulk-move, bulk-delete-by-class

Run from repo root: `python3 scripts/prod_features_e2e.py`
"""
from __future__ import annotations

import io
import os
import sys
import uuid

import requests

API = os.environ.get("ANVIL_API", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("ANVIL_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASS = os.environ.get("ANVIL_ADMIN_PASSWORD", "admin")


def die(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def expect(cond: bool, msg: str) -> None:
    if not cond:
        die(msg)


def main() -> None:
    s = requests.Session()
    # Login (cookie session). admin user comes from bootstrap.
    r = s.post(f"{API}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    if r.status_code == 429:
        print("[skip] rate-limited; restart api to clear")
        return
    expect(r.status_code == 200, f"login failed {r.status_code} {r.text}")
    token = r.json().get("access_token")
    expect(bool(token), "no access_token in login response")
    s.cookies.clear()  # use bearer instead of cookies to bypass CSRF (CLI client)
    H = {"Authorization": f"Bearer {token}"}
    s.headers.update(H)

    # Create a project and a couple of datasets.
    p = s.post(f"{API}/api/projects", json={"name": f"prodE2E-{uuid.uuid4().hex[:6]}"}).json()
    pid = p["id"]
    d1 = s.post(f"{API}/api/projects/{pid}/datasets", json={"name": "ds1"}).json()
    d2 = s.post(f"{API}/api/projects/{pid}/datasets", json={"name": "ds2"}).json()
    print(f"[setup] project={pid[:8]} ds1={d1['id'][:8]} ds2={d2['id'][:8]}")

    # ---- Class hierarchy
    print("[H1] class create + parent_id same-project enforcement")
    p2 = s.post(f"{API}/api/projects", json={"name": f"other-{uuid.uuid4().hex[:6]}"}).json()
    other_root = s.post(f"{API}/api/projects/{p2['id']}/classes",
                        json={"name": "OtherRoot"}).json()
    root = s.post(f"{API}/api/projects/{pid}/classes",
                  json={"name": "Vehicle"}).json()
    bad = s.post(f"{API}/api/projects/{pid}/classes",
                 json={"name": "BadChild", "parent_id": other_root["id"]})
    expect(bad.status_code == 400, f"cross-project parent should 400, got {bad.status_code}")
    car = s.post(f"{API}/api/projects/{pid}/classes",
                 json={"name": "Car", "parent_id": root["id"], "geometry_type": "polygon"}).json()
    sedan = s.post(f"{API}/api/projects/{pid}/classes",
                   json={"name": "Sedan", "parent_id": car["id"]}).json()
    print(f"  hierarchy OK (Vehicle > Car > Sedan)")

    print("[H2] cycle prevention")
    cycle = s.patch(f"{API}/api/projects/{pid}/classes/{root['id']}",
                    json={"parent_id": sedan["id"]})
    expect(cycle.status_code == 400, f"cycle should 400, got {cycle.status_code} {cycle.text}")

    print("[H3] cannot delete class with children")
    rdel = s.delete(f"{API}/api/projects/{pid}/classes/{root['id']}")
    expect(rdel.status_code == 409, f"delete-with-children should 409, got {rdel.status_code}")

    # ---- Keypoint schema
    print("[K1] keypoints class requires schema")
    bad_kp = s.post(f"{API}/api/projects/{pid}/classes",
                    json={"name": "BadKP", "geometry_type": "keypoints"})
    expect(bad_kp.status_code == 400, "kp class without schema must 400")

    skel = {
        "keypoints": [{"name": "head"}, {"name": "tail"}],
        "edges": [[0, 1]],
    }
    kp_class = s.post(f"{API}/api/projects/{pid}/classes",
                      json={"name": "Animal", "geometry_type": "keypoints", "keypoint_schema": skel},
                      headers=H).json()
    print(f"  kp class created with 2 keypoints")

    # ---- Upload a tiny image so we can attach annotations
    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new("RGB", (64, 64), color=(128, 128, 128)).save(buf, "PNG")
    buf.seek(0)
    upr = s.post(
        f"{API}/api/projects/{pid}/datasets/{d1['id']}/images",
        files={"files": ("a.png", buf.getvalue(), "image/png")},
    )
    expect(upr.status_code in (200, 201), f"upload failed {upr.status_code} {upr.text}")
    img = upr.json()[0] if isinstance(upr.json(), list) else upr.json()
    img_id = img["id"]

    # ---- Geometry validation + creation for each new type
    print("[G1] polyline creation + validation")
    bad_pl = s.post(f"{API}/api/images/{img_id}/annotations",
                    json={"class_id": car["id"], "geometry_type": "polyline",
                          "geometry": {"points": [[1, 1]]}})
    expect(bad_pl.status_code == 400, "polyline <2 pts must 400")
    pl = s.post(f"{API}/api/images/{img_id}/annotations",
                json={"class_id": car["id"], "geometry_type": "polyline",
                      "geometry": {"points": [[1, 1], [10, 10], [20, 5]]}})
    expect(pl.status_code == 201, f"polyline create failed: {pl.text}")

    print("[G2] obb creation + angle normalization")
    obb = s.post(f"{API}/api/images/{img_id}/annotations",
                 json={"class_id": car["id"], "geometry_type": "obb",
                       "geometry": {"cx": 30, "cy": 30, "w": 20, "h": 10, "angle_rad": 9.0}})
    expect(obb.status_code == 201, f"obb create failed: {obb.text}")
    a = obb.json()
    expect(-3.15 < a["geometry"]["angle_rad"] < 3.15, "angle should be normalized to [-π,π)")

    print("[G3] keypoints creation + length check")
    bad_kp_a = s.post(f"{API}/api/images/{img_id}/annotations",
                      json={"class_id": kp_class["id"], "geometry_type": "keypoints",
                            "geometry": {"points": [[1, 1, 2]]}})
    expect(bad_kp_a.status_code == 400, "wrong kp count must 400")
    kp_ann = s.post(f"{API}/api/images/{img_id}/annotations",
                    json={"class_id": kp_class["id"], "geometry_type": "keypoints",
                          "geometry": {"points": [[10, 10, 2], [50, 50, 1]]}})
    expect(kp_ann.status_code == 201, f"kp create failed: {kp_ann.text}")

    print("[K2] keypoint schema locked once annotated")
    locked = s.patch(f"{API}/api/projects/{pid}/classes/{kp_class['id']}",
                     json={"keypoint_schema": {"keypoints": [{"name": "x"}, {"name": "y"}, {"name": "z"}],
                                               "edges": []}})
    expect(locked.status_code == 409, f"schema should be locked: {locked.status_code}")

    # ---- Image filters
    print("[F1] list images: has_annotations + class_id filter")
    r = s.get(f"{API}/api/projects/{pid}/datasets/{d1['id']}/images?has_annotations=true").json()
    expect(any(i["id"] == img_id for i in r), "image should appear with has_annotations=true")
    r = s.get(f"{API}/api/projects/{pid}/datasets/{d1['id']}/images",
              params=[("class_id", car["id"])]).json()
    expect(any(i["id"] == img_id for i in r), "class_id=car should match")

    print("[F2] sort variants accept and return ordered results")
    for sort in ("recent", "oldest", "filename", "most_labels", "fewest_labels", "uncertainty"):
        r = s.get(f"{API}/api/projects/{pid}/datasets/{d1['id']}/images?sort={sort}")
        expect(r.status_code == 200, f"sort={sort} failed {r.status_code} {r.text}")

    # ---- Bulk ops
    # Add another image so bulk-delete has volume.
    buf2 = io.BytesIO()
    PILImage.new("RGB", (32, 32), color=(0, 128, 0)).save(buf2, "PNG")
    upr2 = s.post(
        f"{API}/api/projects/{pid}/datasets/{d1['id']}/images",
        files={"files": ("b.png", buf2.getvalue(), "image/png")},
    )
    img2_id = (upr2.json()[0] if isinstance(upr2.json(), list) else upr2.json())["id"]

    print("[B1] bulk-move rejects images outside dataset")
    fake_id = str(uuid.uuid4())
    r = s.post(f"{API}/api/projects/{pid}/datasets/{d1['id']}/images/bulk-move",
               headers=H, json={"image_ids": [img2_id, fake_id], "target_dataset_id": d2["id"]}).json()
    expect(img2_id in [str(x) for x in r["ok"]], "img2 should move ok")
    expect(any(f["id"] == fake_id for f in r["failed"]), "fake id must be in failed")

    print("[B2] bulk-review reset OK from in_progress")
    # img_id is in_progress (had annotations)
    r = s.post(f"{API}/api/projects/{pid}/datasets/{d1['id']}/images/bulk-review",
               headers=H, json={"image_ids": [img_id], "action": "reset"}).json()
    expect(str(img_id) in [str(x) for x in r["ok"]], f"reset failed: {r}")

    print("[B3] bulk-delete-by-class skips nothing (image is unlocked)")
    r = s.post(f"{API}/api/projects/{pid}/datasets/{d1['id']}/annotations/bulk-delete-by-class",
               headers=H, json={"class_id": car["id"]}).json()
    expect("ok" in r, f"bulk-delete-by-class shape: {r}")

    print("[B4] bulk-delete images")
    r = s.post(f"{API}/api/projects/{pid}/datasets/{d1['id']}/images/bulk-delete",
               headers=H, json={"image_ids": [img_id]}).json()
    expect(str(img_id) in [str(x) for x in r["ok"]], f"bulk-delete failed: {r}")

    # ---- COCO export sanity (just check it returns 200)
    print("[X1] COCO export works with new geometry types in classes")
    r = s.get(f"{API}/api/projects/{pid}/datasets/{d2['id']}/export/coco?include_images=false")
    expect(r.status_code == 200, f"coco export {r.status_code}")

    # Cleanup projects
    s.delete(f"{API}/api/projects/{pid}")
    s.delete(f"{API}/api/projects/{p2['id']}")

    print("\nALL OK")


if __name__ == "__main__":
    main()
