"""End-to-end test for class manager + class properties.

Covers:
  - PATCH class (rename, color, description)
  - Create per-class properties (single, multi, text)
  - Annotate with property values; validation rejects unknown options
  - PATCH annotation properties; partial update; unknown keys dropped
  - Delete property → existing annotations have value stripped
  - Class change wipes properties (different schema)
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
    tok = s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["access_token"]
    s.headers["Authorization"] = f"Bearer {tok}"

    # 1) Project + 2 classes
    proj = s.post(f"{API}/api/projects", json={"name": f"CP {uuid4().hex[:6]}"}).json()
    pid = proj["id"]
    cls = s.post(f"{API}/api/projects/{pid}/classes",
                 json={"name": "cucumber", "color": "#22c55e"}).json()
    cls_b = s.post(f"{API}/api/projects/{pid}/classes",
                   json={"name": "lemon", "color": "#facc15"}).json()
    print(f"➜ project={pid[:8]}…  classes={cls['name']}, {cls_b['name']}")

    # 2) PATCH class — rename/color/description
    r = s.patch(f"{API}/api/projects/{pid}/classes/{cls['id']}",
                json={"name": "Cucumber", "color": "#10b981", "description": "Whole cucumber, top-down view"})
    r.raise_for_status()
    upd = r.json()
    assert upd["name"] == "Cucumber"
    assert upd["color"] == "#10b981"
    assert upd["description"] == "Whole cucumber, top-down view"
    print("✓ patch class: rename + color + description")

    # 3) Create properties (single, multi, text) on cucumber
    p_sev = s.post(f"{API}/api/projects/{pid}/classes/{cls['id']}/properties", json={
        "name": "severity", "type": "single",
        "options": ["mild", "moderate", "severe"], "required": True, "sort_order": 0,
    }).json()
    p_def = s.post(f"{API}/api/projects/{pid}/classes/{cls['id']}/properties", json={
        "name": "defects", "type": "multi",
        "options": ["bruise", "rot", "cut", "discoloration"], "sort_order": 1,
    }).json()
    p_note = s.post(f"{API}/api/projects/{pid}/classes/{cls['id']}/properties", json={
        "name": "notes", "type": "text", "sort_order": 2,
    }).json()
    print(f"✓ created 3 properties: {p_sev['name']}, {p_def['name']}, {p_note['name']}")

    listed = s.get(f"{API}/api/projects/{pid}/classes/{cls['id']}/properties").json()
    assert [p["name"] for p in listed] == ["severity", "defects", "notes"], listed
    print("✓ list returns properties in sort_order")

    # 4) Patch property — rename + add option
    r = s.patch(f"{API}/api/projects/{pid}/classes/{cls['id']}/properties/{p_def['id']}",
                json={"options": ["bruise", "rot", "cut", "discoloration", "wilted"]})
    assert r.status_code == 200
    assert "wilted" in r.json()["options"]
    print("✓ patch property options")

    # 5) Upload image + wait + annotate WITH properties
    ds = s.post(f"{API}/api/projects/{pid}/datasets", json={"name": "D"}).json()
    with open(SAMPLE, "rb") as fh:
        img = s.post(f"{API}/api/projects/{pid}/datasets/{ds['id']}/images",
                     files={"files": (SAMPLE.name, fh, "image/jpeg")}).json()[0]
    for _ in range(60):
        if s.get(f"{API}/api/images/{img['id']}").json()["status"] == "ready":
            break
        time.sleep(1)

    # Validation: unknown SINGLE option → 400
    r = s.post(f"{API}/api/images/{img['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "bbox",
        "geometry": {"x": 0, "y": 0, "w": 50, "h": 50},
        "properties": {p_sev["id"]: "extreme"},
    })
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    print("✓ unknown single option rejected with 400")

    # Validation: bad MULTI value type → 400
    r = s.post(f"{API}/api/images/{img['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "bbox",
        "geometry": {"x": 0, "y": 0, "w": 50, "h": 50},
        "properties": {p_def["id"]: "bruise"},  # should be a list
    })
    assert r.status_code == 400
    print("✓ multi value must be list — rejected")

    # Happy path: create annotation with valid properties
    a = s.post(f"{API}/api/images/{img['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "bbox",
        "geometry": {"x": 0, "y": 0, "w": 50, "h": 50},
        "properties": {
            p_sev["id"]: "moderate",
            p_def["id"]: ["bruise", "wilted"],
            p_note["id"]: "near stem",
            "junkkey": "ignored",  # unknown id should drop silently
        },
    }).json()
    assert a["properties"][p_sev["id"]] == "moderate"
    assert a["properties"][p_def["id"]] == ["bruise", "wilted"]
    assert a["properties"][p_note["id"]] == "near stem"
    assert "junkkey" not in a["properties"]
    print("✓ create annotation with properties; unknown keys dropped")

    # 6) PATCH annotation properties — partial update + clear one
    r = s.patch(f"{API}/api/annotations/{a['id']}", json={
        "properties": {
            p_sev["id"]: "severe",
            p_def["id"]: [],          # empty list → cleared
            # p_note omitted entirely → cleared (full replace semantics)
        },
    })
    upd = r.json()
    assert upd["properties"].get(p_sev["id"]) == "severe"
    assert p_def["id"] not in upd["properties"], upd["properties"]
    assert p_note["id"] not in upd["properties"]
    print("✓ patch annotation properties (full replace)")

    # 7) Class-change wipes properties
    r = s.patch(f"{API}/api/annotations/{a['id']}", json={"class_id": cls_b["id"]})
    upd = r.json()
    assert upd["class_id"] == cls_b["id"]
    assert upd["properties"] == {}, f"expected wiped, got {upd['properties']}"
    print("✓ class change wipes properties")

    # restore class for next assertion
    s.patch(f"{API}/api/annotations/{a['id']}", json={
        "class_id": cls["id"],
        "properties": {p_sev["id"]: "mild"},
    })

    # 8) Delete a property → strips it from existing annotations
    s.delete(f"{API}/api/projects/{pid}/classes/{cls['id']}/properties/{p_sev['id']}")
    after = s.get(f"{API}/api/images/{img['id']}/annotations").json()
    assert all(p_sev["id"] not in (x["properties"] or {}) for x in after)
    print("✓ delete property strips it from all annotations of the class")

    # cleanup
    s.delete(f"{API}/api/projects/{pid}")
    print("\n🎉 class-properties round-trip passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
