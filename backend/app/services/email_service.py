"""
email_service — minimal SMTP sender for the ``notify.email`` workflow node.

Uses the stdlib ``smtplib`` (no new dependency) off the event loop via
``asyncio.to_thread``. Configuration comes from the ``SMTP_*`` settings; an
unset ``SMTP_HOST`` disables sending and raises, so a workflow node fails
loudly (and its retry / onError policy applies) instead of silently dropping
mail.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.smtp_host)


def _send_sync(to: list[str], subject: str, body: str) -> None:
    sender = settings.smtp_from or settings.smtp_user
    if not sender:
        raise RuntimeError("SMTP_FROM (or SMTP_USER) must be set to send email")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


async def send_email(to: str | list[str], subject: str, body: str) -> dict:
    """Send a plain-text email. Returns {sent, to}; raises on any failure."""
    if not is_configured():
        raise RuntimeError("SMTP is not configured (set SMTP_HOST)")
    raw = [to] if isinstance(to, str) else list(to)
    recipients = [p.strip() for item in raw for p in str(item).split(",") if p.strip()]
    if not recipients:
        raise ValueError("notify.email: 'to' is required")
    await asyncio.to_thread(_send_sync, recipients, subject, body)
    logger.info("email sent to=%s subject=%r", recipients, subject[:80])
    return {"sent": True, "to": recipients}
