"""Provider registry with memory-aware lazy loading.

SAM2 (~2.1GB) and SAM3 (~3.5GB) both resident is ~5.6GB. On the default 9.7GB
Docker VM that leaves only ~2.6GB for Postgres, MinIO, Redis, api, worker and
web — enough to work, but not enough to be careless.

So models load on first use, and when the budget would be exceeded the
least-recently-used provider is released first. This keeps a laptop-sized
deployment alive instead of letting the kernel OOM-kill the ML container,
which is unrecoverable and logs nothing.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from anvil_ml.providers.base import Capabilities, ModelProvider
from anvil_ml.providers.sam2_provider import SAM2Provider
from anvil_ml.providers.sam3_provider import SAM3Provider

log = logging.getLogger("anvil.ml.registry")

_PROVIDERS: dict[str, ModelProvider] = {
    "sam2": SAM2Provider(),
    "sam3": SAM3Provider(),
}

DEFAULT_PROVIDER = "sam2"

_lock = threading.RLock()
_last_used: dict[str, float] = {}


def _budget_gb() -> float:
    """Resident model budget. Override with ANVIL_MODEL_MEMORY_BUDGET_GB."""
    try:
        return float(os.getenv("ANVIL_MODEL_MEMORY_BUDGET_GB", "6.0"))
    except ValueError:
        return 6.0


def available() -> list[str]:
    return list(_PROVIDERS)


def list_capabilities() -> dict[str, Any]:
    """Capability map for the frontend, plus current residency."""
    return {
        "default": DEFAULT_PROVIDER,
        "budget_gb": _budget_gb(),
        "models": {
            name: {
                **p.capabilities().to_dict(),
                "loaded": p.is_loaded(),
            }
            for name, p in _PROVIDERS.items()
        },
    }


def _resident_gb(exclude: str | None = None) -> float:
    return sum(
        p.capabilities().approx_memory_gb
        for n, p in _PROVIDERS.items()
        if p.is_loaded() and n != exclude
    )


def _make_room_for(name: str) -> None:
    """Release LRU providers until `name` fits in the budget."""
    want = _PROVIDERS[name].capabilities().approx_memory_gb
    budget = _budget_gb()

    while _resident_gb(exclude=name) + want > budget:
        loaded = [
            n for n, p in _PROVIDERS.items() if p.is_loaded() and n != name
        ]
        if not loaded:
            return
        victim = min(loaded, key=lambda n: _last_used.get(n, 0.0))
        log.warning(
            "releasing_provider_for_memory",
            extra={
                "released": victim,
                "loading": name,
                "budget_gb": budget,
                "resident_gb": round(_resident_gb(), 2),
            },
        )
        _PROVIDERS[victim].release()
        _last_used.pop(victim, None)


def get_provider(name: str | None = None) -> ModelProvider:
    """Return a loaded provider, evicting others if memory requires it."""
    key = (name or DEFAULT_PROVIDER).lower()
    if key not in _PROVIDERS:
        raise KeyError(f"unknown model '{key}'; available: {sorted(_PROVIDERS)}")

    with _lock:
        provider = _PROVIDERS[key]
        if not provider.is_loaded():
            _make_room_for(key)
            provider.load()
        _last_used[key] = time.time()
        return provider


def capabilities_of(name: str) -> Capabilities:
    key = name.lower()
    if key not in _PROVIDERS:
        raise KeyError(f"unknown model '{key}'")
    return _PROVIDERS[key].capabilities()


def release(name: str) -> bool:
    with _lock:
        key = name.lower()
        if key not in _PROVIDERS or not _PROVIDERS[key].is_loaded():
            return False
        _PROVIDERS[key].release()
        _last_used.pop(key, None)
        return True


def release_all() -> None:
    with _lock:
        for name, p in _PROVIDERS.items():
            if p.is_loaded():
                p.release()
                _last_used.pop(name, None)
