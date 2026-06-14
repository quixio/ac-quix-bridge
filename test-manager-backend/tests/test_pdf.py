"""Unit tests for the post-race analysis PDF renderer (shared/post_race_ai/pdf.py).

Asserts a valid PDF blob comes out; content correctness is eyeballed separately
(PDF text streams are compressed, so substring assertions are unreliable).
"""

from datetime import datetime, timezone

import pytest

from api.models import Analysis, Anomaly, KpiValue, RequirementCheck
from shared.post_race_ai.pdf import _safe_url_fetcher, render_analysis_pdf


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


def test_render_returns_pdf_bytes() -> None:
    pdf = render_analysis_pdf(_complete_analysis())
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_render_handles_empty_content() -> None:
    analysis = _complete_analysis()
    analysis.kpis = []
    analysis.anomalies = []
    analysis.requirements_check = []
    analysis.summary_md = ""

    pdf = render_analysis_pdf(analysis)
    assert pdf[:4] == b"%PDF"
