#!/usr/bin/env python3
"""Load one local video into the `/model` SAM3 + Ollama showcase.

The upload returns immediately; the API then converts HEVC/MOV to a
browser-streamable H.264 MP4 in the background. This command waits until that
preparation finishes and prints the browser link.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

API = os.getenv("ANVIL_API", "http://127.0.0.1:8000")
EMAIL = os.getenv("ANVIL_ADMIN_EMAIL", "admin@anvil.dev")
PASSWORD = os.getenv("ANVIL_ADMIN_PASSWORD", "admin")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    path = args.video.expanduser().resolve()
    if not path.is_file():
        parser.error(f"video does not exist: {path}")

    session = requests.Session()
    login = session.post(
        f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30
    )
    login.raise_for_status()
    session.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

    existing = session.get(f"{API}/api/showcase/videos", timeout=30)
    existing.raise_for_status()
    match = next(
        (
            row
            for row in existing.json()
            if row["filename"] == path.name and row["bytes"] == path.stat().st_size
        ),
        None,
    )
    if match and match["status"] == "ready":
        print(f"Already ready: {match['filename']}")
        print("Open: http://localhost:5173/model")
        return 0

    print(f"Uploading {path.name} ({path.stat().st_size / 1024 / 1024:.1f} MB) ...")
    with path.open("rb") as handle:
        response = session.post(
            f"{API}/api/showcase/videos",
            data=handle,
            headers={
                "X-Filename": path.name,
                "Content-Type": "video/quicktime",
            },
            timeout=600,
        )
    response.raise_for_status()
    video = response.json()
    print(f"Uploaded. Preparing browser playback (id={video['id']}) ...")

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        status = session.get(
            f"{API}/api/showcase/videos/{video['id']}", timeout=30
        )
        status.raise_for_status()
        video = status.json()
        if video["status"] == "ready":
            print(
                f"Ready: {video['width']}x{video['height']}, "
                f"{video['duration_s']:.1f}s, {video['fps']:.1f} fps"
            )
            print("Open: http://localhost:5173/model")
            return 0
        if video["status"] == "failed":
            sys.exit(f"Video preparation failed: {video['error']}")
        print(".", end="", flush=True)
        time.sleep(4)
    sys.exit(f"Timed out after {args.timeout}s waiting for video preparation")


if __name__ == "__main__":
    raise SystemExit(main())
