"""E2E smoketest for bbox resize: create a bbox, PATCH geometry several
times (simulating handle drags), confirm undo reverts to the original
geometry and redo replays.

Run:  python scripts/bbox_resize_e2e.py
"""
from __future__ import annotations

import os
import sys
from copy import deepcopy
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

    proj = s.post(f"{API}/api/projects", json={"name": f"BBOX-E2E {uuid4().hex[:6]}"}).json()
    cls = s.post(f"{API}/api/projects/{proj['id']}/classes",
                 json={"name": "thing", "color": "#22c55e"}).json()
    ds = s.post(f"{API}/api/projects/{proj['id']}/datasets", json={"name": "B"}).json()
    with open(SAMPLE, "rb") as fh:
        img = s.post(f"{API}/api/projects/{proj['id']}/datasets/{ds['id']}/images",
                     files={"files": (SAMPLE.name, fh, "image/jpeg")}).json()[0]

    # 1) Create a bbox at (50, 60, 100, 80)
    g0 = {"x": 50.0, "y": 60.0, "w": 100.0, "h": 80.0}
    a = s.post(f"{API}/api/images/{img['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "bbox",
        "geometry": g0, "source": "manual",
    }).json()
    aid = a["id"]
    print(f"✓ created bbox id={aid[:8]}… geom={a['geometry']}")
    assert a["geometry"] == g0

    # 2) PATCH a few times (simulates dragging SE handle to grow, then N to shrink)
    g1 = {"x": 50.0, "y": 60.0, "w": 150.0, "h": 120.0}
    a1 = s.patch(f"{API}/api/annotations/{aid}", json={"geometry": g1}).json()
    assert a1["geometry"] == g1
    print(f"✓ patch #1 (SE drag) → {a1['geometry']}")

    g2 = {"x": 50.0, "y": 80.0, "w": 150.0, "h": 100.0}
    a2 = s.patch(f"{API}/api/annotations/{aid}", json={"geometry": g2}).json()
    assert a2["geometry"] == g2
    print(f"✓ patch #2 (N drag)  → {a2['geometry']}")

    # 3) Move the box by patching x, y (simulates body drag)
    g3 = {"x": 200.0, "y": 200.0, "w": 150.0, "h": 100.0}
    a3 = s.patch(f"{API}/api/annotations/{aid}", json={"geometry": g3}).json()
    assert a3["geometry"] == g3
    print(f"✓ patch #3 (body drag) → {a3['geometry']}")

    # 4) Undo simulation: replay reverse PATCHes one by one (like the UI does)
    history = [(a, a1), (a1, a2), (a2, a3)]   # (before, after) pairs
    for before, after in reversed(history):
        rev = s.patch(f"{API}/api/annotations/{aid}", json={"geometry": before["geometry"]}).json()
        assert rev["geometry"] == before["geometry"], f"expected {before['geometry']}, got {rev['geometry']}"
    print(f"✓ undo×3 → back to original {g0}")
    cur = s.get(f"{API}/api/images/{img['id']}/annotations").json()
    assert len(cur) == 1 and cur[0]["geometry"] == g0

    # 5) Redo simulation
    for before, after in history:
        rev = s.patch(f"{API}/api/annotations/{aid}", json={"geometry": after["geometry"]}).json()
        assert rev["geometry"] == after["geometry"]
    print(f"✓ redo×3 → {g3}")
    cur = s.get(f"{API}/api/images/{img['id']}/annotations").json()
    assert cur[0]["geometry"] == g3

    s.delete(f"{API}/api/projects/{proj['id']}")
    print("\n🎉 bbox resize / move E2E passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
