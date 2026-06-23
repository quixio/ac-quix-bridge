"""Tests for F4 — best-effort post-race email notification on analysis complete."""

from datetime import datetime, timezone
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from api import notify
from api.models import Analysis
from api.mongo import get_mongo
from api.routes.mcp.handlers.write import save_analysis
from shared.post_race_ai import email as email_mod
from tests.conftest import TestFactory


class _FakeSMTP:
    """Records what send_email_with_pdf does, in place of a real SMTP server."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int = 0, context: Any = None):
        self.host = host
        self.port = port
        self.context = context
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: Any = None
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self, context: Any = None) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, msg: Any) -> None:
        self.sent = msg


def _insert_analysis(
    test_id: str,
    *,
    status: str = "complete",
    session_id: str | None = "2026-01-01T00:00:00Z",
    aid: str = "a-email-1",
    triggered_by: str | None = None,
) -> Analysis:
    now = datetime.now(timezone.utc)
    doc = Analysis(
        _id=aid,
        test_id=test_id,
        session_id=session_id,
        triggered_by=cast(Any, triggered_by),
        status=cast(Any, status),
        summary_md="## ok",
        created_at=now,
        updated_at=now,
    )
    get_mongo().analyses.insert_one(doc.model_dump(by_alias=True))
    stored = get_mongo().analyses.find_one({"_id": aid})
    assert stored is not None
    return Analysis(**stored)


def _stub_email(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub SMTP/PDF so the test exercises orchestration, not real send/render."""
    captured: dict[str, Any] = {}

    def _send(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(notify, "smtp_configured", lambda: True)
    monkeypatch.setattr(notify, "render_analysis_pdf", lambda a, telemetry_svg=None: b"PDFBYTES")
    monkeypatch.setattr(notify, "build_analysis_telemetry_svg", lambda a, t, table: None)
    monkeypatch.setattr(notify, "send_email_with_pdf", _send)
    return captured


def test_emails_pdf_to_resolved_driver(
    client: TestClient,
    create_test: TestFactory,
    create_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_driver(name="Ada Lovelace")  # → ada.lovelace@example.com
    _, test = create_test(driver="Ada Lovelace")
    analysis = _insert_analysis(test["test_id"])
    captured = _stub_email(monkeypatch)

    notify.email_completed_analysis(get_mongo(), analysis)

    assert captured["to"] == "ada.lovelace@example.com"
    assert captured["pdf"] == b"PDFBYTES"
    assert test["test_id"] in captured["filename"]
    assert captured["filename"].endswith(".pdf")


def test_skips_when_no_driver_email(
    client: TestClient,
    create_test: TestFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Test references a driver name with no matching Driver doc → no email.
    _, test = create_test(driver="Ghost Driver")
    analysis = _insert_analysis(test["test_id"])
    captured = _stub_email(monkeypatch)

    notify.email_completed_analysis(get_mongo(), analysis)

    assert "to" not in captured  # send_email_with_pdf never called


def test_skips_when_smtp_unconfigured(
    client: TestClient,
    create_test: TestFactory,
    create_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_driver(name="Grace Hopper")
    _, test = create_test(driver="Grace Hopper")
    analysis = _insert_analysis(test["test_id"])
    captured = _stub_email(monkeypatch)
    monkeypatch.setattr(notify, "smtp_configured", lambda: False)

    notify.email_completed_analysis(get_mongo(), analysis)

    assert "to" not in captured


def test_never_raises_on_send_failure(
    client: TestClient,
    create_test: TestFactory,
    create_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_driver(name="Alan Turing")
    _, test = create_test(driver="Alan Turing")
    analysis = _insert_analysis(test["test_id"])
    _stub_email(monkeypatch)

    def _boom(**kwargs: Any) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notify, "send_email_with_pdf", _boom)

    # Must swallow — a failed email can never break the analysis pipeline.
    notify.email_completed_analysis(get_mongo(), analysis)


def test_save_analysis_auto_triggers_email(
    client: TestClient,
    create_test: TestFactory,
    create_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """save_analysis auto-emails ONLY for auto-triggered runs, with the
    now-complete analysis."""
    create_driver(name="Edsger Dijkstra")
    _, test = create_test(driver="Edsger Dijkstra")
    _insert_analysis(
        test["test_id"], status="pending", aid="a-pending-1", triggered_by="auto"
    )

    seen: dict[str, Any] = {}

    def _spy(mongo: Any, analysis: Analysis) -> None:
        seen["analysis"] = analysis

    monkeypatch.setattr("api.routes.mcp.handlers.write.email_completed_analysis", _spy)

    save_analysis(get_mongo(), analysis_id="a-pending-1", summary_md="# done")

    assert seen["analysis"].id == "a-pending-1"
    assert seen["analysis"].status == "complete"


def test_save_analysis_manual_does_not_auto_email(
    client: TestClient,
    create_test: TestFactory,
    create_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual runs are NOT auto-emailed — they go out via the explicit endpoint."""
    create_driver(name="Edsger Dijkstra")
    _, test = create_test(driver="Edsger Dijkstra")
    _insert_analysis(
        test["test_id"], status="pending", aid="a-manual-1", triggered_by="manual"
    )

    called = False

    def _spy(mongo: Any, analysis: Analysis) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("api.routes.mcp.handlers.write.email_completed_analysis", _spy)

    save_analysis(get_mongo(), analysis_id="a-manual-1", summary_md="# done")

    assert called is False


# --- Manual send endpoints ------------------------------------------------ #


def test_recipient_endpoint_returns_driver_email(
    client: TestClient,
    create_test: TestFactory,
    create_driver: Any,
) -> None:
    create_driver(name="Ada Lovelace")
    _, test = create_test(driver="Ada Lovelace")
    _insert_analysis(test["test_id"], aid="a-rcpt-1")

    r = client.get("/api/v1/analyses/a-rcpt-1/recipient")

    assert r.status_code == 200
    assert r.json() == {"email": "ada.lovelace@example.com", "has_email": True}


def test_recipient_endpoint_no_email(
    client: TestClient,
    create_test: TestFactory,
) -> None:
    _, test = create_test(driver="Ghost Driver")
    _insert_analysis(test["test_id"], aid="a-rcpt-2")

    r = client.get("/api/v1/analyses/a-rcpt-2/recipient")

    assert r.status_code == 200
    assert r.json() == {"email": None, "has_email": False}


def test_manual_email_sends_and_returns_recipient(
    client: TestClient,
    create_test: TestFactory,
    create_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_driver(name="Ada Lovelace")
    _, test = create_test(driver="Ada Lovelace")
    _insert_analysis(test["test_id"], aid="a-send-1")
    captured = _stub_email(monkeypatch)

    r = client.post("/api/v1/analyses/a-send-1/email")

    assert r.status_code == 200
    assert r.json() == {"sent": True, "email": "ada.lovelace@example.com"}
    assert captured["to"] == "ada.lovelace@example.com"


def test_manual_email_409_when_not_complete(
    client: TestClient,
    create_test: TestFactory,
    create_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_driver(name="Ada Lovelace")
    _, test = create_test(driver="Ada Lovelace")
    _insert_analysis(test["test_id"], status="pending", aid="a-send-2")
    _stub_email(monkeypatch)

    r = client.post("/api/v1/analyses/a-send-2/email")

    assert r.status_code == 409


def test_manual_email_422_when_no_driver_email(
    client: TestClient,
    create_test: TestFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, test = create_test(driver="Ghost Driver")
    _insert_analysis(test["test_id"], aid="a-send-3")
    _stub_email(monkeypatch)  # SMTP configured, but the driver has no email

    r = client.post("/api/v1/analyses/a-send-3/email")

    assert r.status_code == 422


# --- SMTP transport (shared.post_race_ai.email) --------------------------- #


def _smtp_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    base = {"SMTP_HOST": "mx.example", "SMTP_USER": "u", "SMTP_PASSWORD": "p"}
    base.update(overrides)
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_STARTTLS", "SMTP_SSL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in base.items():
        monkeypatch.setenv(k, v)


def test_send_starttls_path_logs_in_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    _smtp_env(monkeypatch, SMTP_STARTTLS="true")
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(email_mod.smtplib, "SMTP", _FakeSMTP)

    email_mod.send_email_with_pdf(
        to="a@b.co", subject="s", body="b", pdf=b"%PDF", filename="r.pdf"
    )

    inst = _FakeSMTP.instances[-1]
    assert inst.started_tls is True
    assert inst.logged_in == ("u", "p")
    assert inst.sent["To"] == "a@b.co"
    assert inst.sent["Subject"] == "s"


def test_send_uses_ssl_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _smtp_env(monkeypatch, SMTP_SSL="true", SMTP_STARTTLS="false")
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", _FakeSMTP)

    email_mod.send_email_with_pdf(
        to="a@b.co", subject="s", body="b", pdf=b"%PDF", filename="r.pdf"
    )

    inst = _FakeSMTP.instances[-1]
    assert inst.context is not None  # SMTP_SSL got the TLS context
    assert inst.started_tls is False  # implicit TLS — no STARTTLS
    assert inst.logged_in == ("u", "p")


def test_send_refuses_cleartext_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials set but neither STARTTLS nor SSL → refuse rather than leak."""
    _smtp_env(monkeypatch, SMTP_STARTTLS="false")  # SMTP_SSL unset
    monkeypatch.setattr(email_mod.smtplib, "SMTP", _FakeSMTP)

    with pytest.raises(RuntimeError):
        email_mod.send_email_with_pdf(
            to="a@b.co", subject="s", body="b", pdf=b"%PDF", filename="r.pdf"
        )
