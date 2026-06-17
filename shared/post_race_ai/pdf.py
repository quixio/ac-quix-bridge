"""Render a post-race Analysis to a PDF report.

`weasyprint` + `markdown` are imported lazily inside `render_analysis_pdf` so
importing this module (and the analyses route) does NOT require the native
Pango libraries unless a PDF is actually generated.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.models import Analysis

_CSS = """
@page { size: A4; margin: 1.8cm 0; @top-right { content: element(runhead); vertical-align: middle; } }
@page :first { margin-top: 0; @top-right { content: none; } }
.runhead { position: running(runhead); padding-right: 1.6cm; text-align: right; }
.runhead svg { width: 62px; height: auto; display: inline-block; }
.content { padding: 0 1.6cm; }
body { margin: 0; font-family: "DejaVu Sans", sans-serif; font-size: 11px; color: #1a1a1b; }
h1 { font-size: 20px; margin: 0 0 2px; color: #0064ff; }
.meta { color: #646471; font-size: 10px; margin-bottom: 16px; }
h2 { font-size: 13px; color: #222229; border-bottom: 2px solid #0064ff; padding-bottom: 3px; margin: 18px 0 8px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #e3e3f2; vertical-align: top; }
th { background: #f3f5fb; font-size: 10px; text-transform: uppercase; letter-spacing: .3px; color: #434352; }
.sev-error { color: #d12d2d; font-weight: bold; }
.sev-warn { color: #b06f00; font-weight: bold; }
.sev-info { color: #0064ff; }
.summary :is(h1,h2,h3) { font-size: 12px; color: #222229; border-bottom: none; }
.summary table { font-size: 10px; }
.muted { color: #787886; }
.band { background: #0a0b24; border-bottom: 3px solid #ff7828; margin: 0 0 18px 0; padding: 16px 1.6cm; overflow: hidden; }
.band svg { width: 88px; height: auto; float: right; display: block; }
"""

# Quix wordmark (white letters + brand-color dots) for the dark report band.
# Vendored beside this module so it ships in the image and on the bind mount.
try:
    _LOGO_SVG = (Path(__file__).parent / "quix-logo.svg").read_text(encoding="utf-8")
except OSError:
    _LOGO_SVG = ""

# Dark-letter variant for the running header (sits on the white page margin).
_LOGO_DARK = _LOGO_SVG.replace('fill="white"', 'fill="#0a0b24"')


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _fmt_session(session_id: str | None) -> str:
    """Session timestamp → '15 Jun 2026, 11:50 UTC'; 'Test-wide' when null."""
    if not session_id:
        return "Test-wide (all sessions)"
    try:
        dt = datetime.fromisoformat(session_id.replace("Z", "+00:00"))
    except ValueError:
        return session_id
    return dt.strftime("%-d %b %Y, %H:%M UTC")


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

    extras = " · ".join(
        analysis.extra[k]
        for k in ("driver", "track", "car_model")
        if isinstance(analysis.extra.get(k), str) and analysis.extra.get(k)
    )
    summary_html = (
        f'<div class="summary">{md.markdown(analysis.summary_md, extensions=["tables"])}</div>'
        if analysis.summary_md
        else '<p class="muted">No summary.</p>'
    )
    sess = _fmt_session(analysis.session_id)
    if analysis.session_id:
        sess = f"Session {sess}"
    meta = f"{_esc(analysis.test_id)} · {_esc(sess)}"
    if extras:
        meta += f" · {_esc(extras)}"
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
{f'<div class="runhead">{_LOGO_DARK}</div>' if _LOGO_SVG else ""}
{f'<div class="band">{_LOGO_SVG}</div>' if _LOGO_SVG else ""}
<div class="content">
<h1>Post-Race Analysis</h1>
<div class="meta">{meta}</div>
<h2>Summary</h2>{summary_html}
<h2>KPIs</h2>{_kpi_table(analysis)}
{_requirements_table(analysis)}
{_anomalies_table(analysis)}
</div>
</body></html>"""
    return HTML(string=doc, url_fetcher=_safe_url_fetcher).write_pdf()
