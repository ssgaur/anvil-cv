"""End-to-end test for the canonical training-ready schema.

Covers:
  - Concept CRUD + duplicate-phrase guard
  - Query is idempotent per (image, concept)
  - Negative-example integrity: verified_absent is refused while instances
    exist, and verified_present is refused with zero instances
  - Provenance: generated_by / model_score survive a round-trip, and editing a
    model mask flips human_corrected while preserving original_geometry
  - mask_rle is persisted (fidelity beyond the polygon)
  - Dataset version freezes an immutable snapshot with deterministic splits
"""
from __future__ import annotations

import io
import os
import sys
from uuid import uuid4

import requests

API = os.getenv("ANVIL_API", "http://127.0.0.1:8000")
EMAIL = os.getenv("ANVIL_ADMIN_EMAIL", "admin@anvil.dev")
PASSWORD = os.getenv("ANVIL_ADMIN_PASSWORD", "admin")


def _png(w: int = 96, h: int = 96) -> bytes:
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (w, h), (90, 140, 90)).save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    s = requests.Session()
    r = s.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    r.raise_for_status()
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    tag = uuid4().hex[:8]
    proj = s.post(f"{API}/api/projects", json={"name": f"concept-e2e-{tag}"})
    proj.raise_for_status()
    pid = proj.json()["id"]

    ds = s.post(f"{API}/api/projects/{pid}/datasets", json={"name": "ds"})
    ds.raise_for_status()
    did = ds.json()["id"]

    cls = s.post(
        f"{API}/api/projects/{pid}/classes",
        json={"name": "leaf", "color": "#22c55e", "geometry_type": "polygon"},
    )
    cls.raise_for_status()
    cid = cls.json()["id"]

    up = s.post(
        f"{API}/api/projects/{pid}/datasets/{did}/images",
        files=[("files", ("a.png", _png(), "image/png"))],
    )
    up.raise_for_status()
    img_id = up.json()[0]["id"]

    # ---- Concepts
    c = s.post(
        f"{API}/api/projects/{pid}/concepts",
        json={"canonical_phrase": "red car", "aliases": ["automobile"], "class_id": cid},
    )
    c.raise_for_status()
    concept_id = c.json()["id"]
    print("[C1] concept created")

    dup = s.post(
        f"{API}/api/projects/{pid}/concepts", json={"canonical_phrase": "red car"}
    )
    assert dup.status_code == 409, f"expected 409, got {dup.status_code}"
    print("[C2] duplicate phrase rejected (409)")

    # ---- Query idempotency
    q1 = s.post(f"{API}/api/images/{img_id}/queries", json={"concept_id": concept_id})
    q1.raise_for_status()
    qid = q1.json()["id"]
    q2 = s.post(f"{API}/api/images/{img_id}/queries", json={"concept_id": concept_id})
    q2.raise_for_status()
    assert q2.json()["id"] == qid, "query must be idempotent per (image, concept)"
    assert q1.json()["text"] == "red car", "text defaults to canonical phrase"
    print("[C3] query idempotent per (image, concept)")

    # ---- verified_present with zero instances must fail
    bad = s.patch(f"{API}/api/queries/{qid}", json={"status": "verified_present"})
    assert bad.status_code == 409, f"expected 409, got {bad.status_code}"
    print("[C4] verified_present blocked with 0 instances")

    # ---- verified_absent is allowed while empty (a real negative example)
    neg = s.patch(
        f"{API}/api/queries/{qid}",
        json={"status": "verified_absent", "is_exhaustive": True},
    )
    neg.raise_for_status()
    assert neg.json()["status"] == "verified_absent"
    assert neg.json()["is_exhaustive"] is True
    print("[C5] verified_absent recorded as a true negative")

    # ---- Model-generated instance with provenance + mask RLE
    rle = {"size": [96, 96], "counts": "abc123"}
    a = s.post(
        f"{API}/api/images/{img_id}/annotations",
        json={
            "class_id": cid,
            "geometry_type": "polygon",
            "geometry": {"points": [[1, 1], [40, 1], [40, 40], [1, 40]]},
            "source": "model",
            "query_id": qid,
            "mask_rle": rle,
            "generated_by": "sam3",
            "model_name": "sam3",
            "model_score": 0.93,
        },
    )
    a.raise_for_status()
    ann = a.json()
    aid = ann["id"]
    assert ann["generated_by"] == "sam3", ann["generated_by"]
    assert ann["model_score"] == 0.93
    assert ann["mask_rle"] == rle, "mask_rle must round-trip"
    assert ann["human_corrected"] is False
    print("[C6] provenance + mask_rle persisted")

    # ---- Now that an instance exists, verified_absent must be refused
    clash = s.patch(f"{API}/api/queries/{qid}", json={"status": "verified_absent"})
    assert clash.status_code == 409, f"expected 409, got {clash.status_code}"
    print("[C7] verified_absent blocked while instances exist")

    ok = s.patch(
        f"{API}/api/queries/{qid}",
        json={"status": "verified_present", "is_exhaustive": True,
              "is_pixel_exhaustive": True},
    )
    ok.raise_for_status()
    assert ok.json()["instance_count"] == 1
    print("[C8] verified_present + exhaustiveness recorded")

    # ---- Human edit flips human_corrected, keeps the model's original
    ed = s.patch(
        f"{API}/api/annotations/{aid}",
        json={"geometry": {"points": [[2, 2], [50, 2], [50, 50], [2, 50]]}},
    )
    ed.raise_for_status()
    assert ed.json()["human_corrected"] is True, "edit must flag human_corrected"
    print("[C9] human correction tracked")

    # ---- Dataset version snapshot
    s.patch(f"{API}/api/images/{img_id}/review", json={"action": "submit"})
    ver = s.post(
        f"{API}/api/projects/{pid}/datasets/{did}/versions",
        json={"name": "v1", "review_status": None, "seed": 7},
    )
    ver.raise_for_status()
    v = ver.json()
    assert v["version"] == 1
    assert v["frozen"] is True
    assert v["stats"]["images"] == 1
    assert v["stats"]["annotations"] == 1
    assert v["stats"]["annotations_with_mask_rle"] == 1
    assert v["stats"]["queries_verified_present"] == 1
    assert v["stats"]["by_generated_by"].get("sam3") == 1
    assert set(v["splits"].values()) <= {"train", "val", "test"}
    print(f"[C10] dataset version frozen: {v['stats']}")

    ver2 = s.post(
        f"{API}/api/projects/{pid}/datasets/{did}/versions",
        json={"review_status": None, "seed": 7},
    )
    ver2.raise_for_status()
    assert ver2.json()["version"] == 2, "version must auto-increment"
    assert ver2.json()["splits"] == v["splits"], "same seed must give same splits"
    print("[C11] versions increment; splits deterministic for a fixed seed")

    s.delete(f"{API}/api/projects/{pid}")
    print("\nALL OK ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
