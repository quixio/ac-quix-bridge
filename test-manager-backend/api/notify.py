"""Best-effort email notification when a post-race analysis completes (F4).

Hooked into `save_analysis` (the single point where an analysis flips to
`complete`). Resolves the test's driver email via the `name_key` join, renders
the F2 PDF, and emails it. Best-effort throughout: a missing recipient or a
send failure is logged and swallowed so it can never break the pipeline.
"""

import logging
from typing import Any

from pymongo.database import Database

from shared.post_race_ai.email import send_email_with_pdf, smtp_configured
from shared.post_race_ai.pdf import analysis_pdf_filename, render_analysis_pdf
from shared.post_race_ai.telemetry_viz import build_analysis_telemetry_svg

from .models import Analysis, Test
from .settings import get_settings
from .text import driver_name_key

logger = logging.getLogger(__name__)


class EmailNotConfigured(Exception):
    """Raised when SMTP_HOST is unset — the server cannot send mail."""


class NoRecipientEmail(Exception):
    """Raised when the test's driver has no resolvable email."""


def resolve_driver_email(mongo: Database[dict[str, Any]], test_id: str) -> str | None:
    """The email of the test's driver, joined by folded name_key, or None."""
    test = mongo.tests.find_one({"_id": test_id}, {"driver": 1})
    if not test or not test.get("driver"):
        return None
    key = driver_name_key(test["driver"])
    driver = mongo.drivers.find_one({"name_key": key}, {"email": 1})
    if not driver:
        # Log the computed key so a name mismatch (rename / odd casing) is
        # diagnosable rather than a silent skip.
        logger.info(
            "[email] no driver matches name_key=%r (test.driver=%r)",
            key,
            test["driver"],
        )
        return None
    return driver.get("email") or None


def send_analysis_email(mongo: Database[dict[str, Any]], analysis: Analysis) -> str:
    """Render the analysis PDF and email it to the test's driver; return the recipient.

    Raises `EmailNotConfigured` (no SMTP), `NoRecipientEmail` (driver has no
    email), or an `smtplib` error on send failure. The manual-send route surfaces
    these to the user; the auto path wraps and swallows them.
    """
    if not smtp_configured():
        raise EmailNotConfigured("SMTP_HOST not set")
    email = resolve_driver_email(mongo, analysis.test_id)
    if not email:
        raise NoRecipientEmail(f"no driver email for test {analysis.test_id}")

    telemetry_svg = None
    try:
        test_doc = mongo.tests.find_one({"_id": analysis.test_id})
        if test_doc:
            telemetry_svg = build_analysis_telemetry_svg(
                analysis, Test(**test_doc), get_settings().telemetry_table_name
            )
    except Exception:
        logger.warning("[email] telemetry build failed for %s", analysis.id, exc_info=True)
    pdf = render_analysis_pdf(analysis, telemetry_svg=telemetry_svg)
    filename = analysis_pdf_filename(analysis)
    subject = "Thanks for visiting the Quix booth at the Automotive Testing Expo 2026"
    body = (
        "Your post-race analysis is ready, find it attached to this email.\n\n"
        "To discuss your results and learn more about Quix, book a session with our "
        "test engineering team here: https://quix.io/book-a-demo/"
    )
    send_email_with_pdf(
        to=email, subject=subject, body=body, pdf=pdf, filename=filename
    )
    logger.info("[email] sent analysis %s to %s", analysis.id, email)
    return email


def email_completed_analysis(
    mongo: Database[dict[str, Any]], analysis: Analysis
) -> None:
    """Auto-email a completed analysis to the driver. Best-effort: never raises.

    Skips (logged) when SMTP is unconfigured or no driver email resolves.
    """
    try:
        send_analysis_email(mongo, analysis)
    except EmailNotConfigured:
        logger.info("[email] SMTP not configured — skipping analysis %s", analysis.id)
    except NoRecipientEmail:
        logger.info(
            "[email] no driver email for test %s — skipping analysis %s",
            analysis.test_id,
            analysis.id,
        )
    except Exception as exc:  # best-effort — must never break save_analysis
        logger.error("[email] failed for analysis %s: %s", analysis.id, exc)
