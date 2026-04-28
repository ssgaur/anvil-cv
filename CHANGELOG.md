# Changelog

All notable changes to Anvil are documented here.

This project follows the spirit of [Keep a Changelog](https://keepachangelog.com/)
and uses semantic-ish versioning while it is in alpha.

## [Unreleased]

### Added

- OSS launch-readiness docs, security policy, code of conduct, demo recording
  plan, sample-data loader, docs-site config, and GHCR release workflow.

## [0.1.0-alpha] - 2026-04-28

### Added

- React/Konva workview with polygon, bounding-box, brush, polyline, rotated
  bounding-box, keypoint, comment-pin, and SAM2 click-to-segment tools.
- SAM2 embedding pre-compute worker so click-to-segment is instant after images
  are ready.
- SAM2 LoRA fine-tuning loop with model versions, validation metrics, and A/B
  comparison.
- Project, dataset, image upload, class manager, per-annotation properties,
  review statuses, and spatial comments.
- COCO ZIP import/export, including Anvil extensions for polyline and OBB.
- Class hierarchy, keypoint schemas, server-side image filters/sort, and bulk
  dataset operations.
- Production-shaped auth: first-run setup wizard, RBAC roles, HttpOnly cookie
  sessions, rotating refresh tokens, password reset, GitHub OAuth, CSRF checks,
  and in-process rate limiting.
- Operational hardening: `/health`, `/ready`, `/metrics`, JSON request logs,
  backup/restore scripts, Docker Compose smoke CI, and typed web build CI.

### Notes

- Alpha software: APIs and database shape can still change before a stable 1.0.
- Google OAuth is intentionally not enabled by default; GitHub OAuth is wired.

