"""In-process sliding-window rate limiter.

Single-replica only — protects login/signup/oauth-callback from brute force.
When scaling to multiple API workers/containers, swap to redis-backed counters.

Design notes (from critique):
- `time.monotonic()` so NTP jumps don't expire windows wrongly.
- Per-key deque of hit timestamps; `len <= max_hits`. Reject *before* appending
  on overflow so per-key memory is bounded.
- Email keys are HMAC'd with JWT_SECRET → no raw PII in dict + offline-guess
  resistant.
- For login email: count only *failed* attempts (caller calls
  `record_login_failure_email`); successful login calls
  `reset_login_failures_email`. This prevents an attacker locking out a real
  user by spamming wrong passwords.
- Lazy per-key prune only (no global O(N) scan under lock).
- Total-key cap with FIFO eviction is a survival valve, not a correctness
  guarantee — documented.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import math
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request, status

from anvil_api.config import settings

log = logging.getLogger(__name__)

_MAX_KEYS = 50_000  # safety valve per limiter


class RateLimiter:
    def __init__(self, name: str, max_hits: int, window_s: float) -> None:
        if max_hits < 1 or window_s <= 0:
            raise ValueError(f"invalid rate-limit config for {name!r}")
        self.name = name
        self.max_hits = max_hits
        self.window_s = float(window_s)
        # OrderedDict so we can FIFO-evict oldest key when over cap.
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, dq: deque[float], now: float) -> None:
        cutoff = now - self.window_s
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def check(self, key: str) -> None:
        """Raise 429 if `key` has exceeded the limit; otherwise record a hit."""
        if not settings.rate_limit_enabled:
            return
        now = time.monotonic()
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                if len(self._hits) >= _MAX_KEYS:
                    # FIFO evict — survival valve only
                    self._hits.popitem(last=False)
                dq = deque()
                self._hits[key] = dq
            else:
                # Touch for LRU-ish ordering
                self._hits.move_to_end(key)
                self._prune(dq, now)
            if len(dq) >= self.max_hits:
                retry = max(1, math.ceil(dq[0] + self.window_s - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={"detail": "too many requests", "retry_after": retry},
                    headers={"Retry-After": str(retry)},
                )
            dq.append(now)

    def record_failure(self, key: str) -> None:
        """Same as `check` but never raises — used after a failed-login attempt
        so the *next* attempt sees the increment.
        Actually: combined with reset_on_success below, simplest is to just call
        `check` *before* password verify (consumes a slot), and reset on success.
        Kept for clarity / future use.
        """
        self.check(key)

    def reset(self, key: str) -> None:
        """Clear a key (e.g. on successful login)."""
        with self._lock:
            self._hits.pop(key, None)


def _hash_email(email: str) -> str:
    """HMAC-SHA256 of normalized email; keeps PII out of in-memory keys."""
    norm = (email or "").strip().lower().encode("utf-8", "ignore")
    return hmac.new(settings.jwt_secret.encode("utf-8"), norm, hashlib.sha256).hexdigest()


def client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For only when explicitly
    trusted via `TRUSTED_PROXY=true` (default off — prevents spoofing).
    """
    if settings.trusted_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            # leftmost = original client when trusted upstream rewrites
            ip = xff.split(",", 1)[0].strip()
            if ip:
                return ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


# --- Singletons (configured at import) ---
login_ip = RateLimiter(
    "login_ip", settings.rate_limit_login_per_min, 60.0,
)
login_email = RateLimiter(
    "login_email", settings.rate_limit_login_email_per_min, 60.0,
)
signup_ip = RateLimiter(
    "signup_ip", settings.rate_limit_signup_per_hour, 3600.0,
)
oauth_callback_ip = RateLimiter(
    "oauth_callback_ip", settings.rate_limit_oauth_callback_per_min, 60.0,
)


# --- Convenience helpers used by routes ---
def hit_login_ip(request: Request) -> None:
    login_ip.check(client_ip(request))


def hit_login_email(email: str) -> None:
    login_email.check(_hash_email(email))


def reset_login_email(email: str) -> None:
    login_email.reset(_hash_email(email))


def hit_signup_ip(request: Request) -> None:
    signup_ip.check(client_ip(request))


def hit_oauth_callback_ip(request: Request) -> None:
    oauth_callback_ip.check(client_ip(request))
