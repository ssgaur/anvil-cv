"""Smoke-test the deployed `/model` SAM3 object-removal editor.

Set ANVIL_REMOVAL_E2E=1 to run a real half-second SAM3 → SAM2 tracking →
ProPainter → MP4 job. The default checks runtime availability, prepared video
metadata and byte-range playback.
"""

from __future__ import annotations

import os
import sys
import time
from urllib.parse import urljoin
from uuid import uuid4

import requests

API = os.getenv("ANVIL_API", "http://127.0.0.1:8000")
EMAIL = os.getenv("ANVIL_ADMIN_EMAIL", "admin@anvil.dev")
PASSWORD = os.getenv("ANVIL_ADMIN_PASSWORD", "admin")


def main() -> int:
    session = requests.Session()
    login = session.post(
        f"{API}/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    login.raise_for_status()
    session.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

    runtime = session.get(f"{API}/api/showcase/runtime", timeout=30)
    runtime.raise_for_status()
    engines = runtime.json()
    assert engines["sam3"] is True, engines
    assert engines.get("personal_worker", {}).get("propainter") is True, engines
    print("✓ runtime: SAM3 + native-MPS ProPainter worker")

    response = session.get(f"{API}/api/showcase/videos", timeout=30)
    response.raise_for_status()
    videos = response.json()
    ready = next((video for video in videos if video["status"] == "ready"), None)
    assert ready is not None, "no ready showcase video; run scripts/load_showcase_video.py"
    assert ready["duration_s"] > 0
    assert ready["playback_url"]
    print(
        f"✓ video: {ready['filename']} {ready['width']}x{ready['height']} "
        f"{ready['duration_s']:.1f}s"
    )

    playback = urljoin(API, ready["playback_url"])
    chunk = requests.get(playback, headers={"Range": "bytes=0-1023"}, timeout=30)
    assert chunk.status_code == 206, chunk.status_code
    assert chunk.headers.get("content-type") == "video/mp4"
    assert len(chunk.content) == 1024
    print("✓ playback: MP4 byte-range seeking")

    if os.getenv("ANVIL_REMOVAL_E2E") == "1":
        started = time.monotonic()
        discovery = session.post(
            f"{API}/api/showcase/videos/{ready['id']}/discover",
            json={"timestamp_s": 20, "concept": "person", "threshold": 0.3},
            timeout=900,
        )
        discovery.raise_for_status()
        instances = discovery.json()["instances"]
        assert instances, "SAM3 found no person at 20s"
        create = session.post(
            f"{API}/api/showcase/videos/{ready['id']}/removals",
            json={
                "request_id": str(uuid4()),
                "concept": "person",
                "start_s": 19.75,
                "end_s": 20.25,
                "seed_s": 20,
                "selected_scores": [item["score"] for item in instances[:2]],
                "mask_rles": [item["mask_rle"] for item in instances[:2]],
            },
            timeout=30,
        )
        create.raise_for_status()
        job = create.json()
        deadline = time.monotonic() + 600
        while job["status"] not in ("completed", "failed") and time.monotonic() < deadline:
            time.sleep(3)
            poll = session.get(
                f"{API}/api/showcase/removals/{job['id']}", timeout=30
            )
            poll.raise_for_status()
            job = poll.json()
        assert job["status"] == "completed", job
        assert job["output_url"]
        assert job["original_preserved"] is True
        print(
            f"✓ removal ({time.monotonic() - started:.1f}s): "
            "SAM3 mask → SAM2 track → ProPainter → derived MP4"
        )

    print("\nALL OK ✓  Open http://localhost:5173/model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
