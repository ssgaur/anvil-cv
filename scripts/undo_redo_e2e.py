"""End-to-end undo/redo simulation against the live API.

Mirrors what the WorkviewPage does:
  1. Login, create project + class + dataset + upload cucumber image
  2. Create annotation A (push 'create' op)
  3. Update geometry of A         (push 'update' op)
  4. Delete A                     (push 'delete' op)
  5. Walk the stack backward (undo undo undo) and confirm state
  6. Walk forward (redo redo redo) and confirm state again
  7. Verify mid-stack mutation correctly clears redo
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


def remap(stack, old_id, new_ann):
    out = []
    for op in stack:
        op = deepcopy(op)
        if op["kind"] in ("create", "delete") and op["ann"]["id"] == old_id:
            op["ann"] = new_ann
        elif op["kind"] == "update" and op["before"]["id"] == old_id:
            op["before"]["id"] = new_ann["id"]
            op["after"]["id"] = new_ann["id"]
        out.append(op)
    return out


def main() -> int:
    if not SAMPLE.exists():
        print(f"missing sample image at {SAMPLE}")
        return 2
    s = requests.Session()
    s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).raise_for_status()
    tok = s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["access_token"]
    s.headers["Authorization"] = f"Bearer {tok}"

    proj = s.post(f"{API}/api/projects", json={"name": f"UR-E2E {uuid4().hex[:6]}"}).json()
    cls = s.post(f"{API}/api/projects/{proj['id']}/classes",
                 json={"name": "cucumber", "color": "#22c55e"}).json()
    ds = s.post(f"{API}/api/projects/{proj['id']}/datasets", json={"name": "B"}).json()
    with open(SAMPLE, "rb") as fh:
        img = s.post(f"{API}/api/projects/{proj['id']}/datasets/{ds['id']}/images",
                     files={"files": (SAMPLE.name, fh, "image/jpeg")}).json()[0]

    def list_anns():
        return s.get(f"{API}/api/images/{img['id']}/annotations").json()

    undo, redo = [], []

    def push(op):
        undo.append(op)
        redo.clear()

    def apply_inverse(op):
        if op["kind"] == "create":
            s.delete(f"{API}/api/annotations/{op['ann']['id']}").raise_for_status()
            return {"kind": "delete", "ann": op["ann"]}
        if op["kind"] == "delete":
            r = s.post(f"{API}/api/images/{img['id']}/annotations", json={
                "class_id": op["ann"]["class_id"],
                "geometry_type": op["ann"]["geometry_type"],
                "geometry": op["ann"]["geometry"],
                "source": op["ann"].get("source", "manual"),
            })
            r.raise_for_status()
            new_ann = r.json()
            old_id = op["ann"]["id"]
            undo[:] = remap(undo, old_id, new_ann)
            redo[:] = remap(redo, old_id, new_ann)
            return {"kind": "create", "ann": new_ann}
        # update
        s.patch(f"{API}/api/annotations/{op['before']['id']}",
                      json={"geometry": op["before"]["geometry"]}).json()
        return {"kind": "update", "before": op["after"], "after": op["before"]}

    def do_undo():
        op = undo.pop()
        redo.append(apply_inverse(op))

    def do_redo():
        op = redo.pop()
        undo.append(apply_inverse(op))

    # ---- Step 1: create polygon A
    poly1 = [[100.0, 100.0], [200.0, 100.0], [200.0, 200.0], [100.0, 200.0]]
    A = s.post(f"{API}/api/images/{img['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "polygon",
        "geometry": {"points": poly1}, "source": "manual",
    }).json()
    push({"kind": "create", "ann": A})
    assert len(list_anns()) == 1, "after create: expected 1 ann"
    print(f"✓ created A id={A['id'][:8]}…")

    # ---- Step 2: update A's geometry
    poly2 = [[110.0, 110.0], [210.0, 110.0], [210.0, 210.0], [110.0, 210.0]]
    A2 = s.patch(f"{API}/api/annotations/{A['id']}", json={"geometry": {"points": poly2}}).json()
    push({"kind": "update", "before": A, "after": A2})
    assert list_anns()[0]["geometry"]["points"][0] == [110.0, 110.0]
    print("✓ updated A geometry")

    # ---- Step 3: delete A
    s.delete(f"{API}/api/annotations/{A2['id']}").raise_for_status()
    push({"kind": "delete", "ann": A2})
    assert list_anns() == []
    print("✓ deleted A")

    # ---- Step 4: undo x3 → back to original create
    do_undo()  # undo delete → re-create (NEW id)
    assert len(list_anns()) == 1
    re_id = list_anns()[0]["id"]
    print(f"✓ undo delete → re-created with new id={re_id[:8]}…")

    do_undo()  # undo update → revert geometry to poly1
    cur = list_anns()[0]
    assert cur["geometry"]["points"][0] == [100.0, 100.0], f"expected revert, got {cur['geometry']['points'][0]}"
    print("✓ undo update → geometry reverted")

    do_undo()  # undo create → delete
    assert list_anns() == [], f"expected empty, got {list_anns()}"
    print("✓ undo create → ann deleted")
    assert undo == [], "undo stack should be empty"
    assert len(redo) == 3, f"redo stack should have 3, has {len(redo)}"

    # ---- Step 5: redo x3 → back to deleted state
    do_redo()  # redo create
    assert len(list_anns()) == 1
    print("✓ redo create")
    do_redo()  # redo update
    assert list_anns()[0]["geometry"]["points"][0] == [110.0, 110.0]
    print("✓ redo update")
    do_redo()  # redo delete
    assert list_anns() == []
    print("✓ redo delete")
    assert redo == []

    # ---- Step 6: undo once, then make a NEW mutation → redo stack must clear
    do_undo()  # undo delete → re-create
    assert len(list_anns()) == 1
    assert len(redo) == 1
    new_ann = s.post(f"{API}/api/images/{img['id']}/annotations", json={
        "class_id": cls["id"], "geometry_type": "bbox",
        "geometry": {"x": 5, "y": 5, "w": 50, "h": 50}, "source": "manual",
    }).json()
    push({"kind": "create", "ann": new_ann})
    assert redo == [], "redo must clear after a fresh mutation"
    assert len(list_anns()) == 2
    print("✓ fresh mutation cleared redo stack")

    # ---- Cleanup
    s.delete(f"{API}/api/projects/{proj['id']}")
    print("\n🎉 Undo/redo E2E passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
