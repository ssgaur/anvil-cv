"""Tiny pluggable email sender.

Backends:
  * ``log`` — writes the message to stdout. Dev default; perfect for local
    work and CI (the test grabs it from the API logs to assert the link).
  * ``smtp`` — uses ``smtplib`` with EMAIL_SMTP_* env vars. Production.

We deliberately don't pull in a heavy templating engine — the only template
right now is the password-reset link. Add more functions as needs grow.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from anvil_api.config import settings

log = logging.getLogger("anvil.email")


def _send_log(to: str, subject: str, body: str) -> None:
    log.info("EMAIL[stdout]\n  to: %s\n  subject: %s\n  body:\n%s", to, subject, body)


def _send_smtp(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    host = settings.email_smtp_host or "localhost"
    port = int(settings.email_smtp_port or 587)
    with smtplib.SMTP(host, port, timeout=15) as s:
        if settings.email_smtp_starttls:
            s.starttls()
        if settings.email_smtp_user:
            s.login(settings.email_smtp_user, settings.email_smtp_password)
        s.send_message(msg)


def send_email(to: str, subject: str, body: str) -> None:
    """Dispatch to the configured backend. Never raises into the request
    path: a delivery failure must NOT signal account existence to the user."""
    backend = (settings.email_backend or "log").lower()
    try:
        if backend == "smtp":
            _send_smtp(to, subject, body)
        else:
            _send_log(to, subject, body)
    except Exception:
        log.exception("email send failed (backend=%s, to=%s)", backend, to)


def password_reset_email(to: str, link: str, *, oauth_only: bool = False) -> tuple[str, str]:
    """Build (subject, body) for a reset email. Pure function for testability."""
    if oauth_only:
        subject = "Set a password for your Anvil account"
        body = (
            "Hi,\n\n"
            "Someone — hopefully you — asked to set a password for your Anvil "
            "account. Click the link below to choose one. The link expires in "
            f"{settings.password_reset_ttl_minutes} minutes and can only be used "
            "once.\n\n"
            f"{link}\n\n"
            "If you didn't request this, ignore this email — your account stays "
            "as-is and you can keep signing in with your existing provider.\n"
        )
    else:
        subject = "Reset your Anvil password"
        body = (
            "Hi,\n\n"
            "Someone — hopefully you — asked to reset your Anvil password. "
            "Click the link below to choose a new one. The link expires in "
            f"{settings.password_reset_ttl_minutes} minutes and can only be used "
            "once.\n\n"
            f"{link}\n\n"
            "If you didn't request this, ignore this email — your password stays "
            "unchanged.\n"
        )
    return subject, body
