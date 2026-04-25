"""End-to-end review-flow + comments test.

Covers:
  - First annotation auto-bumps NEW → IN_PROGRESS
  - submit requires ≥1 annotation
  - state machine: submit / approve / reopen / submit / reject / submit
  - reject requires a reason; auto-attaches a [Reject] comment
  - Spatial comment CRUD (pinned + general); resolve toggle
  - List filter ?review_status=approved
  - COCO export ?review_status=approved excludes others
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


def main() -> int:
    if not SAMPLE.exists():
        print(f"missing sample image at {SAMPLE}")
        return 2

    s = requests.Session()
    tok = s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["access_token"]
    s.headers["Authorization"] = f"Bearer {tok}"

    proj = s.post(f"{API}/api/projects", json={"name": f"REV {uuid4().hex[:6]}"}).json()
    pid = proj["id"]
    cls = s.post(f"{API}/api/projects/{pid}/classes",
                 json={"name": "cucumber", "color": "#22c55e"}).json()
    ds = s.post(f"{API}/api/projects/{pid}/datasets", json={"name": "D"}).json()

    # Two images: one will be approved, one rejected → submitted again
    imgs = []
    for i in range(2):
        with open(SAMPLE, "rb") as fh:
            up = s.post(f"{API}/api/projects/{pid}/datasets/{ds['id']}/images",
                        files={"files": (f"img{i}.jpg", fh, "image/jpeg")}).json()
        imgs.append(up[0])
    for img in imgs:
        for _ in range(60):
            if s.get(f"{API}/api/images/{img['id']}").json()["status"] == "ready":
                break
            time.sleep(1)
    print(f"➜ project={pid[:8]}…  uploaded {len(imgs)} images")

    img_a, img_b = imgs

    # 1) submit with no annotations → 400
    r = s.patch(f"{API}/api/images/{img_a['id']}/review", json={"action": "submit"})
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    print("✓ submit blocked when no annotations")

    # First annotation bumps NEW → IN_PROGRESS
    s.post(f"{API}/api/images/{img_a['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "bbox",
        "geometry": {"x": 10.0, "y": 10.0, "w": 100.0, "h": 80.0},
    }).raise_for_status()
    fresh = s.get(f"{API}/api/images/{img_a['id']}").json()
    assert fresh["review_status"] == "in_progress", fresh["review_status"]
    print("✓ first annotation: new → in_progress")

    # 2) submit (in_progress → submitted)
    r = s.patch(f"{API}/api/images/{img_a['id']}/review", json={"action": "submit"}).json()
    assert r["review_status"] == "submitted"
    assert r["submitted_at"] is not None
    print("✓ submit ok")

    # approve from non-submitted → 409
    r = s.patch(f"{API}/api/images/{img_b['id']}/review", json={"action": "approve"})
    assert r.status_code == 409
    print("✓ approve blocked from non-submitted state")

    # 3) approve
    r = s.patch(f"{API}/api/images/{img_a['id']}/review", json={"action": "approve"}).json()
    assert r["review_status"] == "approved"
    assert r["reviewed_at"] is not None
    print("✓ approve ok")

    # 4) reopen approved → in_progress
    r = s.patch(f"{API}/api/images/{img_a['id']}/review", json={"action": "reopen"}).json()
    assert r["review_status"] == "in_progress"
    assert r["reviewed_at"] is None
    print("✓ reopen ok")

    # 5) submit again, then reject without reason → 400
    s.patch(f"{API}/api/images/{img_a['id']}/review", json={"action": "submit"}).raise_for_status()
    r = s.patch(f"{API}/api/images/{img_a['id']}/review", json={"action": "reject"})
    assert r.status_code == 400
    print("✓ reject without reason → 400")

    # reject with reason
    r = s.patch(f"{API}/api/images/{img_a['id']}/review", json={
        "action": "reject", "reason": "polygon misses top-right",
    }).json()
    assert r["review_status"] == "rejected"
    assert "polygon" in r["review_reason"]
    print("✓ reject with reason ok")

    # Comment auto-attached for the rejection?
    cmts = s.get(f"{API}/api/images/{img_a['id']}/comments").json()
    assert any("[Reject]" in c["body"] for c in cmts), cmts
    print("✓ reject auto-attached a comment")

    # 6) Spatial comment CRUD
    pin = s.post(f"{API}/api/images/{img_a['id']}/comments", json={
        "body": "extend mask here", "x": 120.5, "y": 80.0,
    }).json()
    assert pin["x"] == 120.5 and pin["y"] == 80.0 and pin["resolved"] is False
    assert pin["author_name"]
    print(f"✓ created pinned comment by {pin['author_name']!r}")

    # invalid: x without y
    r = s.post(f"{API}/api/images/{img_a['id']}/comments", json={"body": "bad", "x": 1.0})
    assert r.status_code == 400
    print("✓ x-without-y rejected")

    # patch (resolve)
    r = s.patch(f"{API}/api/comments/{pin['id']}", json={"resolved": True}).json()
    assert r["resolved"] is True
    print("✓ resolve comment ok")

    # delete a general comment
    g = s.post(f"{API}/api/images/{img_a['id']}/comments", json={"body": "general note"}).json()
    s.delete(f"{API}/api/comments/{g['id']}")
    after = s.get(f"{API}/api/images/{img_a['id']}/comments").json()
    assert all(c["id"] != g["id"] for c in after)
    print("✓ delete comment ok")

    # 7) Annotate img_b, submit, approve → both A (rejected) & B (approved)
    s.post(f"{API}/api/images/{img_b['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "bbox",
        "geometry": {"x": 5.0, "y": 5.0, "w": 60.0, "h": 60.0},
    }).raise_for_status()
    s.patch(f"{API}/api/images/{img_b['id']}/review", json={"action": "submit"}).raise_for_status()
    s.patch(f"{API}/api/images/{img_b['id']}/review", json={"action": "approve"}).raise_for_status()

    # 8) Filter list by review_status
    only_app = s.get(f"{API}/api/projects/{pid}/datasets/{ds['id']}/images?review_status=approved").json()
    assert len(only_app) == 1 and only_app[0]["id"] == img_b["id"]
    only_rej = s.get(f"{API}/api/projects/{pid}/datasets/{ds['id']}/images?review_status=rejected").json()
    assert len(only_rej) == 1 and only_rej[0]["id"] == img_a["id"]
    print("✓ ?review_status filter works")

    # 9) COCO export ?review_status=approved → only B's annotation
    r = s.get(f"{API}/api/projects/{pid}/datasets/{ds['id']}/export/coco?review_status=approved")
    r.raise_for_status()
    coco = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("annotations.json"))
    assert len(coco["images"]) == 1, coco["images"]
    assert coco["images"][0]["file_name"] == img_b["filename"]
    print(f"✓ coco export filtered: {len(coco['images'])} image(s), {len(coco['annotations'])} ann(s)")

    s.delete(f"{API}/api/projects/{pid}")
    print("\n🎉 Review-flow + comments E2E passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
