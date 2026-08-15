"""Smoke-test the deployed `/model` SAM3 + Ollama video showcase.

Set ANVIL_SHOWCASE_CHAT=1 to run the expensive (~1 minute on M1) visual
question too. The default checks runtime availability, prepared video metadata,
and byte-range playback.
"""

from __future__ import annotations

import os
import sys
import time
from urllib.parse import urljoin

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
    assert engines["ollama"] is True, engines
    print(f"✓ runtime: SAM3 + {engines['ollama_model']}")

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

    if os.getenv("ANVIL_SHOWCASE_CHAT") == "1":
        started = time.monotonic()
        chat = session.post(
            f"{API}/api/showcase/videos/{ready['id']}/chat",
            json={"question": "What is happening in this video?", "current_time_s": 20},
            timeout=900,
        )
        chat.raise_for_status()
        result = chat.json()
        assert result["answer"]
        assert result["ollama_model"] == engines["ollama_model"]
        assert result["segmentation_model"] == "sam3"
        assert result["evidence_url"]
        print(
            f"✓ chat ({time.monotonic() - started:.1f}s): "
            f"{result['answer']} [SAM3 {result['detection_count']} detections]"
        )

    print("\nALL OK ✓  Open http://localhost:5173/model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
