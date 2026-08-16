#!/usr/bin/env python3
"""Install the optional ProPainter personal-use backend outside the repo.

ProPainter uses the S-Lab License 1.0, which permits non-commercial use but
requires separate permission for commercial use. For that reason its source
and weights are installed under gitignored `.anvil-personal/` and are never
included in Anvil releases or Docker images.

Usage:
    python3 scripts/install_propainter_personal.py --accept-noncommercial-license
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".anvil-personal" / "ProPainter"
REPO = "https://github.com/sczhou/ProPainter.git"
RELEASE = "https://github.com/sczhou/ProPainter/releases/download/v0.1.0"
WEIGHTS = ("ProPainter.pth", "recurrent_flow_completion.pth", "raft-things.pth")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept-noncommercial-license", action="store_true")
    args = parser.parse_args()
    if not args.accept_noncommercial_license:
        parser.error(
            "ProPainter is non-commercial. Re-run with "
            "--accept-noncommercial-license after reading "
            "https://github.com/sczhou/ProPainter/blob/main/LICENSE"
        )

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if not (TARGET / ".git").is_dir():
        subprocess.run(["git", "clone", "--depth", "1", REPO, str(TARGET)], check=True)

    weights = TARGET / "weights"
    weights.mkdir(exist_ok=True)
    for name in WEIGHTS:
        output = weights / name
        if not output.is_file():
            print(f"Downloading {name} ...")
            urllib.request.urlretrieve(f"{RELEASE}/{name}", output)

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(TARGET / "requirements.txt")],
        check=True,
    )
    (TARGET.parent / "PROPAINTER_PERSONAL_USE_ONLY").write_text(
        "Accepted S-Lab License 1.0 for personal, non-commercial use only.\n"
    )
    print(f"\nProPainter personal backend ready: {TARGET}")
    print("Start worker: python3 scripts/personal_video_worker.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
