# Post-Race Telemetry Visualization — Design

**Date:** 2026-06-21
**Branch:** `feature/post-race-summary`
**Status:** Design approved, pending spec review → implementation plan

## Goal

Add deterministic per-lap telemetry plots to the post-race analysis, derived directly from the lake (NOT AI-generated). **One renderer, static SVG, two surfaces:**

- **PDF** — appended to the report (WeasyPrint renders SVG fine). Also rides the auto-email attachment.
- **AI Summary tab (frontend card)** — the same SVGs shown as scalable `<img>`, loaded when a finished report is opened.

**Why static (not interactive):** the card already embeds the full Telemetry Explorer (richer interactive charts), so interactive plots here would be redundant. Static SVG = one renderer, zero new frontend dep, vector quality (never pixelated), responsive by default.

## Scope / non-goals

- **Session-level only.** Test-wide analysis (`Analysis.session_id is null`) gets **no viz** in v1.
- **Best-effort.** The entire viz path is wrapped in try/except. Any failure (creds missing, query error, no usable laps, render error) → the viz section is silently omitted; the rest of the report ships unchanged. Never crashes the report or the email.
- No new lake catalog dependency (no `/manifest`); lap list comes from SQL.
- No startup-time credential check; failure surfaces only at call time (and is caught).

## Architecture — 3 layers

```
lake client  ──►  fetch + clean (per-lap series)  ──►  SVG renderer (matplotlib → SVG)
(httpx /query)         (pandas)                              │
                                                   ┌─────────┴─────────┐
                                                 PDF (inline SVG)   endpoint → card <img>
```

1. **Lake client** — `shared/post_race_ai/lake.py` (new). Ports the Telemetry Explorer pattern.
2. **Fetch + clean core** — produces a per-lap series structure. Single source of cleaning logic.
3. **One SVG renderer** (matplotlib, server-side) → the same SVGs go into the PDF and to the card.

### 1. Lake client (creds + query)

Read **exactly two** env vars, no fallback chain:

- `Quix__Lakehouse__Query__Url`
- `Quix__Lakehouse__Query__AuthToken`

If unset/unavailable → raise at call time (caught by the best-effort wrapper). Do **not** validate on startup.

Query call (mirrors `telemetry-comparison/main.py:_lake_query`):
- `POST {url}/query`, body = SQL string, `Content-Type: text/plain`, `Authorization: Bearer {token}`
- Response = CSV → `pandas.read_csv`
- httpx async, ~60s timeout. System SSL defaults (cloud-internal; no byox self-signed handling needed for in-cluster deploys).

### 2. Fetch + clean

**One SQL pulls the whole session** (table from existing `TABLE_NAME` setting):

```sql
SELECT lap, normalizedCarPosition AS pos, speedKmh, gas, brake, gear,
       iCurrentTime, isValidLap, timestamp_ms
FROM <TABLE_NAME>
WHERE session_id = :session_id AND driver = :driver
  AND track = :track AND carModel = :car_model
```

Partition values (session_id, driver, track, car_model) come from `Analysis.context` + the matching `SessionInfo` on the test. `session_id` used verbatim in ISO-Z form (`...T...Z`) so the catalog prunes (equality only — never CAST/LIKE).

**Cleaning, in pandas:**

1. Per-lap aggregates: `n = count`, `lap_ms = MAX(iCurrentTime)`, `n_invalid = count(isValidLap==0)`.
2. **Drop the last lap** (`lap == max(lap)`) — always an unfinished fragment (verified 10/10 sessions; the boundary `1.0` makes `max(pos)` an unreliable "finished" signal, so we drop by lap number, not pos).
3. **Drop slivers**: `n <= 1000`.
4. **Lap-1 staging trim** (TE logic, `telemetry-comparison/main.py:245-264`): in time order, find first wrap (`pos[i-1] > 0.9 and pos[i] < 0.1`); keep samples from `i` onward; re-sort by pos. (Hotlap spawns the car before the s/f line; lap 1 = staging prefix glued to the flying lap, non-monotonic pos. Laps 2+ are clean, no trim.)
5. Sort each lap by `pos`.
6. **Downsample** each lap to ~400 points: bin `pos` into ~400 equal bins, mean per bin.

**Lap metrics:**
- `lap_time` = `MAX(iCurrentTime)` (~18ms short of AC's exact `iLastTime` — acceptable for plot/title).
- `valid` = `n_invalid <= 5` (tolerance, not strict `MIN(isValidLap)==1`). Real cuts span thousands of samples; ≤5 invalid = boundary/staging noise. Verified: only 2 laps lake-wide hit the 1–50 band, both lap-1 of single-lap (dropped) sessions.
- `fastest valid` = min `lap_time` among `valid` laps. None valid → plots 2 & 3 omitted.

**Kept laps** ("clean") = `1 .. max(lap)-1`, minus slivers. Invalid laps **included** (it's a trace, not a leaderboard).

### 3a. Plots (4)

| # | Plot | Data | x | y |
|---|------|------|---|---|
| 1 | Speed | all kept laps overlaid | pos [0,1] | speedKmh |
| 2 | Throttle + brake | fastest valid lap | pos [0,1] | gas, brake (both 0–1) |
| 3 | Gear | fastest valid lap | pos [0,1] | gear (stepped, 1–7) |
| 4 | Lap times | per kept lap | lap # | lap_ms (bar) |

- **Rendered as ONE combined matplotlib figure** — 4 stacked subplots via `gridspec`, height ratios ~3:2:1:2 (speed tallest, gear a thin strip), total figure height tuned to fit one A4 page. Output = a single SVG / single `<img>`.
- Plots 1–3 share the x-axis (pos); plot 4 has its own x (lap #).
- **Subplots are conditional** (data-driven): always speed (1) + lap-times (4) when laps exist; add throttle/brake (2) + gear (3) only when a valid lap exists. `gridspec` is built from the surviving subplots so there are never empty panels.
- Plot 4: valid laps colored, invalid grey, fastest highlighted.
- Quix palette; per-lap distinct colors on plot 1; legend = `L{n} · m:ss.mmm`.
- Per-subplot titles, e.g. "Speed — all laps", "Throttle & brake — fastest lap (L1, 2:24.8)".

### 3b. SVG renderer (shared)

- `matplotlib` (Agg backend, pure-Python, no system libs) → **one SVG** string (the combined 4-subplot figure; vector, never pixelated).
- Figure height fixed to fit one A4 page; conditional subplots via `gridspec` (see Plots).
- Fallback only if SVG fights WeasyPrint: PNG at dpi 200.

### 3c. PDF embedding

- New `<h2>Telemetry</h2>` section appended **after Anomalies** in `shared/post_race_ai/pdf.py` (order: Summary → KPIs → Requirements → Anomalies → **Telemetry**). The single SVG embedded inline / as a data-URI `<img>`.
- **Page-break CSS:** `break-before: page` on the section (starts on a fresh page) + `break-inside: avoid` on the figure (safety; never trimmed). Combined-figure height is tuned so the whole thing fits one page.
- New dep: `matplotlib` in `test-manager-backend/pyproject` + `uv.lock`.

### 3d. Frontend (static, no chart lib)

- New endpoint: `GET /api/v1/analyses/{id}/telemetry` → the rendered SVGs (e.g. JSON `{plots: [{caption, svg}]}`). Built on demand from the same fetch+clean+render path. Best-effort: returns the plots, or an empty signal on any failure.
- Card: new `<section>` `SectionHeading "Telemetry"` at the **bottom** (after summary prose). Each plot an `<img>` of the SVG with `max-width:100%; height:auto`; the SVG carries a `viewBox` → scales/responsive, stays crisp. On failure the section is hidden.
- **No new frontend dependency.** Interactivity (zoom/hover) is already provided by the embedded Telemetry Explorer.

## Lifecycle

- Viz is **decoupled from the AI run** — a separate deterministic lake query built on demand. The AI analysis never waits on the lake; if the lake is down during the run, the report still completes and viz is simply omitted when later viewed/exported.
- Gated on `status == complete` AND session-level (`session_id` not null). `/pdf` already 409s unless complete, so viz only ever appears in finished PDFs; the auto-email only sends on complete.
- **Card**: opening a completed report calls the telemetry endpoint → renders → section appears; not-complete / no session_id / any failure → section hidden.
- **PDF / email**: builds the report and *tries* the viz; any error → the PDF is produced **without** the Telemetry section. Report and email always ship.

## Data sanity + logging

Never render empty placeholders. Layered guards, "omit, don't apologise":

- **0 kept laps** after cleaning → omit the whole Telemetry section.
- **No valid lap** → omit plots 2 & 3 (fastest-lap plots); keep 1 & 4 if laps exist.
- **Per-plot**: empty / all-NaN after sanity → skip that plot. Section renders only if ≥1 plot survives.
- **Sanity clip** on the SQL rows: `speedKmh` 0–400, `gas`/`brake` 0–1, `gear` 0–8, `lap_ms` within ~10s–15min. Out-of-range rows dropped; if a plot empties as a result → skip it.

**Logging (every drop is logged):**
- Each lap dropped, with reason + identity: last-lap, sliver (`n=…`), out-of-range row count.
- Each plot skipped (which + why), section omitted (why).
- Counts, lap numbers, session_id — enough to debug from logs alone. WARNING for unexpected (e.g. creds missing, query error); INFO for normal omits (single-lap session, no valid lap).

## Error handling / best-effort

- One try/except around the whole viz build (fetch → clean → render) per surface (PDF, endpoint).
- Failures logged (`logger.warning(..., exc_info=True)`), section omitted. PDF/email/report/card always succeed.
- "No usable laps" (single-lap session, all slivers) = a normal INFO omit, not an error.

## Performance

- 1 lake query (~0.5–1s) + render. PDF generation goes from sub-second to ~2–4s. Acceptable (on-demand).

## Files touched (anticipated)

- `shared/post_race_ai/lake.py` — new: creds + `_lake_query`.
- `shared/post_race_ai/telemetry_viz.py` — new: fetch + clean → series; matplotlib SVG renderer.
- `shared/post_race_ai/pdf.py` — append Telemetry section.
- `test-manager-backend/api/routes/analyses.py` — new `/telemetry` endpoint.
- `test-manager-backend/pyproject.toml` + `uv.lock` — matplotlib.
- `test-manager-frontend/app/analysis/ai-summary/components/analysis-card.tsx` (+ chart component) — viz section.
- `quix.yaml` — confirm no extra vars needed (rely on auto-inject).

## Deploy notes

- **Verify** `Quix__Lakehouse__Query__*` is auto-injected on **both** byox (prod) and dev. byox confirmed historically; dev to verify. If dev doesn't inject → viz omits on dev (prod = byox, fine).
- matplotlib added to backend image (pure-Agg, no apt libs — unlike WeasyPrint's Pango).
- Frontend build gate if a chart lib is added.

## Verification plan

- Unit: cleaning logic against known sessions (tomas eviltwin Spa = 5 laps → 4 kept, only L1 valid; chimp misano = 4 kept; single-lap session → 0 usable → omit).
- Lap-1 trim: assert non-monotonic raw pos becomes monotonic; staging dropped.
- Render: SVG output is well-formed (`<svg` ... `</svg>`), non-trivial length; PDF still `%PDF`.
- Manual: render a real analysis, eyeball plots vs Telemetry Explorer for the same laps.

## Resolved

- Frontend: static SVG (`<img>`), no chart lib. Interactivity covered by embedded Telemetry Explorer.
- One renderer (matplotlib SVG) serves PDF + card.
- Downsample: ~400 pts/lap (1 per 0.25% of track).
- Responsiveness: `max-width:100%` + SVG `viewBox`.

## Open items (resolve in planning)

1. Color palette specifics + legend formatting (default: Quix palette).
2. Endpoint response shape (`{plots:[{caption, svg}]}` vs inline) + whether to cache/stamp SVGs on the analysis doc vs render on demand.
