"""Render a post-race Analysis to a PDF report.

`weasyprint` + `markdown` are imported lazily inside `render_analysis_pdf` so
importing this module (and the analyses route) does NOT require the native
Pango libraries unless a PDF is actually generated.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.models import Analysis

_CSS = """
@page { size: A4; margin: 1.8cm 1.6cm; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 11px; color: #1a1a1a; }
h1 { font-size: 20px; margin: 0 0 2px; }
.meta { color: #666; font-size: 10px; margin-bottom: 16px; }
h2 { font-size: 13px; border-bottom: 1px solid #ddd; padding-bottom: 3px; margin: 18px 0 8px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #eee; vertical-align: top; }
th { background: #f5f5f5; font-size: 10px; text-transform: uppercase; letter-spacing: .3px; }
.sev-error { color: #b00020; font-weight: bold; }
.sev-warn { color: #b06f00; font-weight: bold; }
.sev-info { color: #555; }
.summary :is(h1,h2,h3) { font-size: 12px; }
.summary table { font-size: 10px; }
.muted { color: #999; }
"""


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _safe_url_fetcher(url: str, *args: object, **kwargs: object) -> dict:
    """SSRF guard for WeasyPrint: allow only `data:` URIs.

    `summary_md` is AI-agent-generated and `markdown` passes raw inline HTML
    through, so an injected `<img src="http://…">` / `file:///…` would
    otherwise make WeasyPrint fetch network/local resources. Our own template
    has no external references, so blocking everything but `data:` is safe.
    """
    if url.startswith("data:"):
        from weasyprint import default_url_fetcher

        return default_url_fetcher(url, *args, **kwargs)  # type: ignore[no-any-return]
    raise ValueError(f"blocked non-data URL in PDF render: {url!r}")


def _kpi_table(analysis: Analysis) -> str:
    if not analysis.kpis:
        return '<p class="muted">No KPIs.</p>'
    rows = "".join(
        f"<tr><td>{_esc(k.name)}</td>"
        f"<td>{_esc(k.value)}{(' ' + _esc(k.unit)) if k.unit else ''}</td>"
        f"<td>{_esc(k.notes)}</td></tr>"
        for k in analysis.kpis
    )
    return f"<table><thead><tr><th>KPI</th><th>Value</th><th>Notes</th></tr></thead><tbody>{rows}</tbody></table>"


def _requirements_table(analysis: Analysis) -> str:
    if not analysis.requirements_check:
        return ""
    verdict = {True: "✓", False: "✗", None: "—"}
    rows = "".join(
        f"<tr><td>{_esc(r.requirement)}</td>"
        f"<td>{verdict[r.met]}</td>"
        f"<td>{_esc(r.evidence)}</td></tr>"
        for r in analysis.requirements_check
    )
    return (
        "<h2>Requirements</h2><table><thead><tr>"
        "<th>Requirement</th><th>Met</th><th>Evidence</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _anomalies_table(analysis: Analysis) -> str:
    if not analysis.anomalies:
        return ""
    rows = "".join(
        f'<tr><td class="sev-{_esc(a.severity)}">{_esc(a.severity)}</td>'
        f"<td>{_esc(a.kind)}</td>"
        f"<td>{_esc(a.lap) if a.lap is not None else ''}</td>"
        f"<td>{_esc(a.description)}</td></tr>"
        for a in analysis.anomalies
    )
    return (
        "<h2>Anomalies</h2><table><thead><tr>"
        "<th>Severity</th><th>Kind</th><th>Lap</th><th>Description</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def render_analysis_pdf(analysis: Analysis) -> bytes:
    """Render a completed Analysis to a PDF report and return the bytes."""
    import markdown as md
    from weasyprint import HTML

    generated = analysis.updated_at.date() if analysis.updated_at else "unknown"
    summary_html = (
        f'<div class="summary">{md.markdown(analysis.summary_md, extensions=["tables"])}</div>'
        if analysis.summary_md
        else '<p class="muted">No summary.</p>'
    )
    scope = analysis.session_id or "test-wide (all sessions)"
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
<h1>Post-Race Analysis</h1>
<div class="meta">{_esc(analysis.test_id)} · {_esc(scope)} · generated {_esc(generated)}</div>
<h2>Summary</h2>{summary_html}
<h2>KPIs</h2>{_kpi_table(analysis)}
{_requirements_table(analysis)}
{_anomalies_table(analysis)}
</body></html>"""
    return HTML(string=doc, url_fetcher=_safe_url_fetcher).write_pdf()
