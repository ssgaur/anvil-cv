"""URL-safe slug helper."""
from __future__ import annotations

import re
import secrets


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text.strip("-") or secrets.token_hex(4)


def unique_slug(base: str, existing: set[str]) -> str:
    s = slugify(base)
    if s not in existing:
        return s
    i = 2
    while f"{s}-{i}" in existing:
        i += 1
    return f"{s}-{i}"
