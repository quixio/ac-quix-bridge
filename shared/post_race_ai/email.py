"""SMTP email of post-race analysis PDFs (F4).

A thin, provider-agnostic smtplib wrapper. The backend's `api.notify` module
resolves the driver + renders the PDF and calls `send_email_with_pdf`; this
module owns only the transport. All config comes from env vars so any provider
(Gmail app-password, SendGrid/SES/Mailgun SMTP) works without code changes.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    """True when at least `SMTP_HOST` is set — the minimum to attempt a send."""
    return bool(os.getenv("SMTP_HOST"))


def send_email_with_pdf(
    *, to: str, subject: str, body: str, pdf: bytes, filename: str
) -> None:
    """Send a plain-text email with a PDF attachment via SMTP.

    Env config: `SMTP_HOST` (required), `SMTP_PORT` (587), `SMTP_USER`,
    `SMTP_PASSWORD`, `SMTP_FROM` (defaults to `SMTP_USER`), `SMTP_TIMEOUT` (20s),
    and one of two TLS modes — `SMTP_SSL` (true → implicit TLS, port 465) or
    `SMTP_STARTTLS` (default true → STARTTLS, port 587). Raises on any SMTP
    failure, and refuses to send credentials over a cleartext connection;
    callers wrap it best-effort so a send error never breaks the pipeline.
    """
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM") or user or "noreply@quix.io"
    use_ssl = os.getenv("SMTP_SSL", "false").lower() == "true"
    use_starttls = os.getenv("SMTP_STARTTLS", "true").lower() == "true"
    timeout = int(os.getenv("SMTP_TIMEOUT", "20"))

    if user and password and not (use_ssl or use_starttls):
        raise RuntimeError(
            "refusing to send SMTP credentials over a cleartext connection — "
            "enable SMTP_SSL (port 465) or SMTP_STARTTLS (port 587)"
        )

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)

    context = ssl.create_default_context()
    if use_ssl:
        server_cm = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        server_cm = smtplib.SMTP(host, port, timeout=timeout)
    with server_cm as server:
        if not use_ssl and use_starttls:
            server.starttls(context=context)
        if user and password:
            server.login(user, password)
        server.send_message(msg)
