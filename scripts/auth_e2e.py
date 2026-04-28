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

    print("[0] GET /api/setup/status")
    r = c.get("/api/setup/status"); r.raise_for_status()
    setup = r.json()
    print(f"  {setup}")
    assert "needs_setup" in setup
    if setup["needs_setup"]:
        print("  → wizard required; creating first admin via /api/setup/initialize")
        first_admin_email = f"setup-admin-{uuid.uuid4().hex[:8]}@example.com"
        r = c.post("/api/setup/initialize", json={
            "email": first_admin_email, "name": "Setup Admin", "password": "supers3cret",
        }); r.raise_for_status()
        # Second call must 409
        r2 = c.post("/api/setup/initialize", json={
            "email": "other@example.com", "name": "Other", "password": "supers3cret",
        })
        assert r2.status_code == 409, r2.status_code
        print("  setup completed; second initialize returns 409 ✓")
    else:
        print("  setup already completed; second initialize returns 409")
        r = c.post("/api/setup/initialize", json={
            "email": "x@example.com", "name": "X", "password": "supers3cret",
        })
        assert r.status_code == 409, r.status_code

    print("\n[1] GET /api/auth/providers")
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
        # httpx Client persists cookies; create a fresh client to truly
        # exercise the unauthenticated path (HttpOnly cookies are otherwise
        # auto-resent by the existing session).
        with httpx.Client(base_url=API, timeout=10.0) as unauth:
            r = unauth.get("/api/auth/me")
            assert r.status_code == 401

        print("\n[7a] new signup defaults to viewer role")
        assert me["role"] == "viewer", me

        print("\n[7b] viewer cannot create projects (403)")
        r = c.post(
            "/api/projects",
            json={"name": "viewer-attempt"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, r.status_code

    # --- RBAC sweep: needs the bootstrap admin (defaults: admin@anvil.dev / admin) ---
    print("\n[R1] admin login (bootstrap)")
    r = c.post("/api/auth/login", json={"email": "admin@anvil.dev", "password": "admin"})
    if r.status_code != 200:
        print(f"  skip RBAC sweep: bootstrap admin login failed ({r.status_code})")
    else:
        admin_tok = r.json()["access_token"]
        ah = {"Authorization": f"Bearer {admin_tok}"}

        r = c.get("/api/auth/me", headers=ah); r.raise_for_status()
        admin_me = r.json()
        assert admin_me["role"] == "admin", admin_me

        suffix = uuid.uuid4().hex[:8]
        labeler_email = f"e2e-lab-{suffix}@example.com"
        reviewer_email = f"e2e-rev-{suffix}@example.com"
        passwd = "supers3cret"

        print("\n[R2] admin creates labeler + reviewer")
        r = c.post("/api/admin/users", headers=ah, json={
            "email": labeler_email, "name": "Lab", "role": "labeler", "password": passwd,
        }); r.raise_for_status()
        lab_user = r.json()
        assert lab_user["role"] == "labeler"
        r = c.post("/api/admin/users", headers=ah, json={
            "email": reviewer_email, "name": "Rev", "role": "reviewer", "password": passwd,
        }); r.raise_for_status()
        rev_user = r.json()

        # Tokens
        lab_tok = c.post("/api/auth/login", json={"email": labeler_email, "password": passwd}).json()["access_token"]
        rev_tok = c.post("/api/auth/login", json={"email": reviewer_email, "password": passwd}).json()["access_token"]
        lh = {"Authorization": f"Bearer {lab_tok}"}
        rh = {"Authorization": f"Bearer {rev_tok}"}

        print("\n[R3] labeler cannot create project (403)")
        r = c.post("/api/projects", json={"name": "lab-fail"}, headers=lh)
        assert r.status_code == 403, r.status_code

        print("\n[R4] admin creates project + dataset (for review tests)")
        r = c.post("/api/projects", json={"name": f"rbac-{suffix}"}, headers=ah); r.raise_for_status()
        proj = r.json()
        r = c.post(f"/api/projects/{proj['id']}/datasets",
                   json={"name": "ds"}, headers=ah); r.raise_for_status()
        ds = r.json()

        print("\n[R5] labeler can list datasets; viewer-style read works for all authed")
        r = c.get(f"/api/projects/{proj['id']}/datasets", headers=lh); r.raise_for_status()

        print("\n[R6] /admin/users access control: labeler 403, admin OK")
        assert c.get("/api/admin/users", headers=lh).status_code == 403
        assert c.get("/api/admin/users", headers=ah).status_code == 200

        print("\n[R7] admin cannot demote self")
        r = c.patch(f"/api/admin/users/{admin_me['id']}", json={"role": "labeler"}, headers=ah)
        assert r.status_code == 409, r.status_code

        print("\n[R8] admin cannot delete self")
        r = c.delete(f"/api/admin/users/{admin_me['id']}", headers=ah)
        assert r.status_code == 409, r.status_code

        print("\n[R9] cleanup created users")
        for uid in (lab_user["id"], rev_user["id"]):
            c.delete(f"/api/admin/users/{uid}", headers=ah)

        # NOTE: full self-approve / image-lock / submit flow needs uploaded image
        # which requires real image bytes + embeddings; out of scope for this smoke.
        print("  (full review-flow self-approve/image-lock checks deferred to manual)")

    print("\n[8] OAuth login route returns 404 when provider unconfigured")
    for prov in ("google", "github"):
        if p["oauth"].get(prov):
            print(f"  {prov} configured — skipping unconfigured-route check")
            continue
        r = c.get(f"/api/auth/oauth/{prov}/login")
        assert r.status_code == 404, f"{prov}: {r.status_code} {r.text}"
    print("  ok")

    print("\n[10] rate-limit: per-email failed-login throttle → 429")
    rl_email = f"rl-{uuid.uuid4().hex[:8]}@example.com"
    last_status = None
    for i in range(1, 12):
        r = c.post("/api/auth/login", json={"email": rl_email, "password": "wrong"})
        last_status = r.status_code
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            print(f"  hit 429 after {i} attempts (Retry-After={ra})")
            assert ra and int(ra) >= 1, ra
            break
    else:
        print(f"  WARN: never got 429 after 11 attempts (rate_limit_enabled may be off); last={last_status}")

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

    # --- Cookie session + refresh + password-reset (uses a fresh client) ---
    print("\n[C1] cookie session: signup, /me via cookie, refresh rotation")
    with httpx.Client(base_url=API, timeout=10.0) as cc:
        cemail = f"e2e-cookie-{uuid.uuid4().hex[:8]}@example.com"
        r = cc.post(
            "/api/auth/signup",
            json={"email": cemail, "name": "C", "password": "supers3cret"},
            headers={"X-Anvil-CSRF": "1"},
        )
        if r.status_code == 429:
            print("  skip cookie tests: signup_ip rate-limit hit")
        else:
            assert r.status_code == 200, r.status_code
            # cookies should be on the client now
            jar = {c.name for c in cc.cookies.jar}
            assert "anvil_access" in jar and "anvil_refresh" in jar, jar
            r = cc.get("/api/auth/me"); assert r.status_code == 200, r.status_code
            assert r.json()["email"] == cemail
            print("  /me via cookie ok")

            r = cc.post(
                "/api/auth/refresh",
                headers={"X-Anvil-CSRF": "1", "Origin": "http://localhost:5173"},
            )
            assert r.status_code == 200, r.status_code
            print("  refresh rotation ok")

            print("\n[C2] password-reset request returns 204 (no enumeration)")
            r = cc.post(
                "/api/auth/password-reset/request",
                json={"email": cemail},
            )
            assert r.status_code == 204, r.status_code
            r = cc.post(
                "/api/auth/password-reset/request",
                json={"email": "nonexistent-user-zzz@example.com"},
            )
            assert r.status_code == 204, r.status_code
            print("  204 for both real and unknown email")

            print("\n[C3] CSRF: cookie + unsafe verb without CSRF header → 403")
            r = cc.post(
                "/api/projects",
                json={"name": "csrf-attempt"},
                headers={"Origin": "http://localhost:5173"},
            )
            assert r.status_code == 403, r.status_code
            print("  ok")

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
