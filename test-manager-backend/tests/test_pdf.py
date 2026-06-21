"""Unit tests for the post-race analysis PDF renderer (shared/post_race_ai/pdf.py).

Asserts a valid PDF blob comes out; content correctness is eyeballed separately
(PDF text streams are compressed, so substring assertions are unreliable).
"""

from datetime import datetime, timezone

import pytest

from api.models import Analysis, AnalysisContext, Anomaly, KpiValue, RequirementCheck
from shared.post_race_ai.pdf import (
    _fmt_date_compact,
    _safe_url_fetcher,
    analysis_pdf_filename,
    render_analysis_pdf,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://internal-service/",
        "file:///etc/passwd",
    ],
)
def test_safe_url_fetcher_blocks_non_data_urls(url: str) -> None:
    """SSRF guard: WeasyPrint must not fetch network/local URLs that injected
    raw HTML in summary_md could reference."""
    with pytest.raises(ValueError):
        _safe_url_fetcher(url)


def _complete_analysis() -> Analysis:
    return Analysis(
        _id="a-1",
        test_id="TST-0001",
        session_id="2026-01-01T00:00:00Z",
        triggered_by="manual",
        status="complete",
        kpis=[
            KpiValue(name="best_lap", value="1:47.560", unit="s", notes="lap 4"),
            KpiValue(name="top_speed", value=213.3, unit="km/h"),
        ],
        requirements_check=[
            RequirementCheck(requirement="lap under 1:50", met=True, evidence="1:47.5")
        ],
        anomalies=[
            Anomaly(
                severity="warn",
                kind="brake_spike",
                lap=3,
                description="hard brake into T1",
            )
        ],
        summary_md="## Summary\n\nGood pace, consistent braking.\n",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.requires_weasyprint
def test_render_returns_pdf_bytes() -> None:
    pdf = render_analysis_pdf(_complete_analysis())
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


@pytest.mark.requires_weasyprint
def test_render_handles_empty_content() -> None:
    analysis = _complete_analysis()
    analysis.kpis = []
    analysis.anomalies = []
    analysis.requirements_check = []
    analysis.summary_md = ""

    pdf = render_analysis_pdf(analysis)
    assert pdf[:4] == b"%PDF"


# --- PDF filename (pure; no weasyprint) ------------------------------------ #


def test_fmt_date_compact() -> None:
    assert _fmt_date_compact("2026-01-01T00:00:00Z") == "1Jan2026"
    assert _fmt_date_compact("2026-06-15T11:50:08.499Z") == "15Jun2026"
    assert _fmt_date_compact(None) is None
    assert _fmt_date_compact("not-a-date") is None


def test_pdf_filename_session_with_track() -> None:
    a = _complete_analysis()  # session_id 2026-01-01
    a.context = AnalysisContext(driver="Daniel", track="Spa", car_model="p991")
    assert analysis_pdf_filename(a) == "Quix-Post-Race-Spa-1Jan2026.pdf"


def test_pdf_filename_no_context_falls_back_to_test_id() -> None:
    a = _complete_analysis()
    a.context = None
    assert analysis_pdf_filename(a) == "Quix-Post-Race-Analysis-TST-0001.pdf"


def test_pdf_filename_test_wide_uses_test_id() -> None:
    a = _complete_analysis()
    a.session_id = None  # test-wide
    a.context = AnalysisContext(driver="Daniel")  # no track
    assert analysis_pdf_filename(a) == "Quix-Post-Race-Analysis-TST-0001.pdf"


def test_pdf_filename_sanitizes_unsafe_chars() -> None:
    a = _complete_analysis()
    a.context = AnalysisContext(track="Spa/Franco rchamps")
    name = analysis_pdf_filename(a)
    assert "/" not in name and " " not in name
    assert name.endswith(".pdf")
