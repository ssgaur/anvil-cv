#!/usr/bin/env python
"""End-to-end smoke for auth: providers, signup, login, /me, duplicate, OAuth 404.

Assumes a fresh-ish API running on :8000 with default settings (signup enabled,
no OAuth providers configured). Safe to run repeatedly: signup uses a unique
email per run.
"""
import sys
import uuid

import httpx

API = "http://127.0.0.1:8000"


def main() -> int:
    c = httpx.Client(base_url=API, timeout=30.0, follow_redirects=False)

    print("[1] GET /api/auth/providers")
    r = c.get("/api/auth/providers")
    r.raise_for_status()
    p = r.json()
    print(f"  {p}")
    assert p["password"] is True
    assert isinstance(p["signup"], bool)
    assert isinstance(p["oauth"], dict)
    assert set(p["oauth"].keys()) == {"google", "github"}

    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    pw = "supers3cret"

    print(f"\n[2] signup (allowed={p['signup']}) email={email}")
    r = c.post("/api/auth/signup", json={"email": email, "name": "E2E", "password": pw})
    if not p["signup"]:
        assert r.status_code == 403, r.text
        print("  signup correctly disabled (403)")
    else:
        r.raise_for_status()
        token = r.json()["access_token"]
        assert token

        print("\n[3] duplicate signup → 409")
        r = c.post(
            "/api/auth/signup",
            json={"email": email, "name": "Dup", "password": pw},
        )
        assert r.status_code == 409, r.status_code

        print("\n[4] login (case-insensitive email)")
        r = c.post("/api/auth/login", json={"email": email.upper(), "password": pw})
        r.raise_for_status()
        token = r.json()["access_token"]

        print("\n[5] /me with token")
        r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        me = r.json()
        assert me["email"] == email
        print(f"  user: {me}")

        print("\n[6] login wrong password → 401")
        r = c.post("/api/auth/login", json={"email": email, "password": "wrong"})
        assert r.status_code == 401

        print("\n[7] /me without token → 401")
        r = c.get("/api/auth/me")
        assert r.status_code == 401

    print("\n[8] OAuth login route returns 404 when provider unconfigured")
    for prov in ("google", "github"):
        if p["oauth"].get(prov):
            print(f"  {prov} configured — skipping unconfigured-route check")
            continue
        r = c.get(f"/api/auth/oauth/{prov}/login")
        assert r.status_code == 404, f"{prov}: {r.status_code} {r.text}"
    print("  ok")

    print("\n[9] OAuth login on configured provider → 302 to provider + nonce cookie")
    for prov in ("google", "github"):
        if not p["oauth"].get(prov):
            continue
        r = c.get(f"/api/auth/oauth/{prov}/login")
        assert r.status_code == 302, f"{prov}: {r.status_code}"
        loc = r.headers.get("location", "")
        assert prov in loc, loc
        assert "anvil_oauth_nonce" in r.headers.get("set-cookie", "")
        print(f"  {prov} ok → {loc[:60]}...")

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.HTTPError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
    except AssertionError as e:
        print(f"ASSERT: {e}", file=sys.stderr)
        sys.exit(2)
