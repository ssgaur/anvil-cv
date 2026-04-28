# Security Policy

## Supported Versions

Anvil is in active alpha. Only the latest `main` and the most recent tagged
release receive security updates.

| Version  | Supported |
| -------- | --------- |
| `main`   | ✅        |
| latest tag | ✅      |
| older    | ❌        |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security problems.**

Instead, report privately via one of:

1. **GitHub Security Advisory** (preferred) — open a draft advisory at
   <https://github.com/ssgaur/anvil-cv/security/advisories/new>. This keeps the
   discussion private until a fix is shipped.
2. **Email** — `security@anvil-cv.dev` (PGP key on request).

Please include:

- A clear description of the vulnerability and its impact
- Steps to reproduce (a minimal docker-compose-based repro is ideal)
- The affected commit SHA or version tag
- Your suggested fix, if any

We aim to acknowledge reports within **3 business days** and ship a fix or
mitigation within **30 days** for high/critical issues. We will credit you in
the release notes unless you prefer to remain anonymous.

## Scope

In scope:

- The Anvil API (`apps/api`), ML service (`apps/ml`), worker (`apps/worker`),
  and web UI (`apps/web`)
- The default `docker-compose.yml` deployment
- The CLI (`anvil-cli`) and backup/restore flow
- Authentication, authorization, CSRF, rate-limiting, file upload handling

Out of scope:

- Self-inflicted misconfiguration (e.g. exposing the API to the public
  internet without TLS, leaving the default `JWT_SECRET`, or running with
  default admin credentials in production)
- Vulnerabilities in third-party dependencies that already have a published
  CVE — please file those upstream
- DoS via expensive operations (model fine-tuning, large COCO imports) when
  the operator has not enabled rate limits

## Hardening Checklist for Operators

When deploying Anvil outside of a trusted local network:

- [ ] Set a strong `JWT_SECRET` (≥ 32 random bytes)
- [ ] Change the default admin password on first run via `/setup`
- [ ] Place behind a TLS-terminating reverse proxy (Caddy, nginx, Traefik)
- [ ] Set `CORS_ORIGINS` to your exact frontend origin (no wildcards)
- [ ] Enable storage backend with encryption-at-rest (S3 SSE, GCS CMEK, etc)
- [ ] Take regular backups via `anvil-cli backup` (cron or systemd timer)
- [ ] Rotate refresh tokens by setting a short `REFRESH_TOKEN_LIFETIME`
- [ ] Subscribe to release notifications: <https://github.com/ssgaur/anvil-cv/releases>

Thank you for helping keep Anvil and its users safe.
