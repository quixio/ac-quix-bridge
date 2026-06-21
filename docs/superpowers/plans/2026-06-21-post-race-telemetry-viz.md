# Post-Race Telemetry Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic per-lap telemetry plots (speed, throttle/brake, gear, lap-times) to the post-race analysis PDF and the AI Summary card, queried directly from the lake.

**Architecture:** One backend data layer (lake client → pandas clean → one combined matplotlib SVG) feeds two surfaces: the PDF (`render_analysis_pdf`) and a new JSON endpoint the card consumes. Entirely best-effort — any failure omits the Telemetry section, never breaks the report/email/card.

**Tech Stack:** Python 3.13, FastAPI, pandas, matplotlib (Agg, SVG), httpx (sync), WeasyPrint (existing), Next.js/React/TypeScript, vitest.

## Global Constraints

- **Best-effort everywhere.** The whole viz path is wrapped in try/except; on any failure the section is omitted and the report/email/card still ship. "No usable laps" is a normal INFO omit, not an error.
- **Two env vars only, no fallback:** `Quix__Lakehouse__Query__Url`, `Quix__Lakehouse__Query__AuthToken`. Unset → raise at call time (caught), never at startup.
- **Session-level only.** Test-wide analysis (`session_id is None`) gets no viz.
- **Cleaning rules:** drop last lap (`lap == max(lap)`); drop slivers (`count <= 1000`); lap-1 staging trim (wrap `pos[i-1]>0.9 and pos[i]<0.1`, keep from `i`); sanity-clip rows (`speedKmh` 0–400, `gas`/`brake` 0–1, `gear` 0–8); downsample to ~400 pos-bins (mean per bin); `valid = n_invalid <= 5`; lap time `= MAX(iCurrentTime)`; fastest valid = min time among valid. Invalid laps **included** in overlays.
- **One combined SVG figure**, A4-fit, conditional subplots (speed + lap-times always; throttle/brake + gear only when a fastest valid lap exists).
- **Log every drop** (which lap + why + counts), session_id in context. WARNING for unexpected, INFO for normal omits.
- **`driver` partition is lowercased** in the lake; `track`/`carModel` are verbatim codes (e.g. `Spa`, `porsche_991ii_gt3_r`).
- **Testing:** backend pytest via `bash scripts/test-backend.sh` (host; sets DYLD + isolated venv). NEVER bare `uv run` in test-manager-backend on host. `ty check` in container: `docker exec ac-quix-backend sh -c "cd /app && uv run ty check"`. ty baseline = 7 pre-existing, tolerate. Frontend: `npm run type-check`/`lint`/`build` (build in a worktree, not the running container) + `npm test` (vitest).
- **Commits:** Conventional Commits `feat(post-race): …`; stage files by name; no push; no `-A`/`.`.
- **New deps:** `pandas`, `matplotlib` in test-manager-backend → requires a dev-image rebuild (`docker compose -f docker-compose.dev.yml up -d --build backend`).

---

### Task 1: Dependencies + cloud blob bind

**Files:**
- Modify: `test-manager-backend/pyproject.toml` (deps), `test-manager-backend/uv.lock`
- Modify: `quix.yaml` (Test Manager - Backend deployment, after line 337)

**Interfaces:**
- Produces: `pandas` + `matplotlib` importable in the backend; `Quix__Lakehouse__Query__*` injected into the deployed backend (via blob bind).

- [ ] **Step 1: Add deps**

Run (host, isolated env so the bind-mounted `.venv` isn't clobbered):
```bash
cd test-manager-backend
UV_PROJECT_ENVIRONMENT=/tmp/tm-test-venv uv add pandas matplotlib
```
Expected: `pyproject.toml` gains `pandas` + `matplotlib`; `uv.lock` updated.

- [ ] **Step 2: Add blob bind to the backend deployment in `quix.yaml`**

After the `TABLE_NAME` variable block of the **Test Manager - Backend** deployment (currently ending at line 337), add at the deployment level (same indentation as the deployment's `variables:` key — 4 spaces):
```yaml
    blobStorage:
      bind: true
```
This is what makes Quix Cloud inject `Quix__Lakehouse__Query__Url` / `__AuthToken` into the backend (mirrors Telemetry Explorer, quix.yaml:488-489). Without it the viz always omits on cloud.

- [ ] **Step 3: Rebuild the dev backend image**

Run:
```bash
docker compose -f docker-compose.dev.yml up -d --build backend
```
Expected: backend container rebuilds with pandas+matplotlib baked in.

- [ ] **Step 4: Verify imports in the container**

Run:
```bash
docker exec ac-quix-backend sh -c "cd /app && python -c 'import pandas, matplotlib; matplotlib.use(\"Agg\"); import matplotlib.pyplot; print(\"ok\")'"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add test-manager-backend/pyproject.toml test-manager-backend/uv.lock quix.yaml
git commit -m "build(post-race): add pandas+matplotlib + lake blob bind for telemetry viz"
```

---

### Task 2: Lake query client

**Files:**
- Create: `shared/post_race_ai/lake.py`
- Test: `test-manager-backend/tests/test_lake.py`

**Interfaces:**
- Produces: `lake_query(sql: str, *, timeout: float = 60.0) -> pandas.DataFrame`. Raises `RuntimeError` if creds unset, `httpx.HTTPStatusError` on non-2xx.

- [ ] **Step 1: Write the failing tests**

Create `test-manager-backend/tests/test_lake.py`:
```python
import pytest

from shared.post_race_ai import lake


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_lake_query_missing_creds_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("Quix__Lakehouse__Query__Url", raising=False)
    monkeypatch.delenv("Quix__Lakehouse__Query__AuthToken", raising=False)
    with pytest.raises(RuntimeError):
        lake.lake_query("SELECT 1")


def test_lake_query_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Quix__Lakehouse__Query__Url", "http://lake")
    monkeypatch.setenv("Quix__Lakehouse__Query__AuthToken", "tok")
    captured: dict[str, object] = {}

    def fake_post(url, content, headers, timeout):  # noqa: ANN001, ANN202
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResp("lap,pos\n1,0.5\n1,0.6\n")

    monkeypatch.setattr(lake.httpx, "post", fake_post)
    df = lake.lake_query("SELECT lap,pos FROM t WHERE x=1")
    assert captured["url"] == "http://lake/query"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert list(df.columns) == ["lap", "pos"]
    assert len(df) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/test-backend.sh tests/test_lake.py -v`
Expected: FAIL — `ModuleNotFoundError: shared.post_race_ai.lake`.

- [ ] **Step 3: Implement `lake.py`**

Create `shared/post_race_ai/lake.py`:
```python
"""QuixLake query client for post-race telemetry visualization.

Reads the two auto-injected lakehouse query vars (no fallback) and POSTs raw
SQL to the lake's /query endpoint, returning the CSV reply as a DataFrame.
Missing credentials raise at call time; callers wrap the viz in best-effort
try/except so the report never crashes.
"""

from __future__ import annotations

import io
import logging
import os

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

_QUERY_URL_VAR = "Quix__Lakehouse__Query__Url"
_QUERY_TOKEN_VAR = "Quix__Lakehouse__Query__AuthToken"


def lake_query(sql: str, *, timeout: float = 60.0) -> pd.DataFrame:
    """Run a SELECT against the lake /query endpoint; return the CSV as a DataFrame."""
    url = os.environ.get(_QUERY_URL_VAR)
    token = os.environ.get(_QUERY_TOKEN_VAR)
    if not url or not token:
        raise RuntimeError(
            f"lakehouse query creds unset ({_QUERY_URL_VAR}/{_QUERY_TOKEN_VAR})"
        )
    logger.info("[lake] POST %s/query (%d chars sql)", url, len(sql))
    resp = httpx.post(
        f"{url}/query",
        content=sql.encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
        timeout=timeout,
    )
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    logger.info("[lake] query -> %d rows", len(df))
    return df
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_lake.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add shared/post_race_ai/lake.py test-manager-backend/tests/test_lake.py
git commit -m "feat(post-race): add lake query client for telemetry viz"
```

---

### Task 3: Data structures, lap-time format, SQL builder

**Files:**
- Create: `shared/post_race_ai/telemetry_viz.py`
- Test: `test-manager-backend/tests/test_telemetry_viz.py`

**Interfaces:**
- Produces:
  - `@dataclass Lap(lap:int, pos:list[float], speed:list[float], gas:list[float], brake:list[float], gear:list[float], lap_ms:int, valid:bool)`
  - `@dataclass LapSeries(laps:list[Lap], fastest_valid_idx:int|None)`
  - `format_lap_ms(ms:int) -> str` → `"m:ss.mmm"`
  - `build_session_sql(table:str, session_id:str, driver:str, track:str, car_model:str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `test-manager-backend/tests/test_telemetry_viz.py`:
```python
import pytest

from shared.post_race_ai import telemetry_viz as tv


def test_format_lap_ms() -> None:
    assert tv.format_lap_ms(144795) == "2:24.795"
    assert tv.format_lap_ms(5000) == "0:05.000"
    assert tv.format_lap_ms(0) == "0:00.000"


def test_build_session_sql_clauses() -> None:
    sql = tv.build_session_sql("ac_telemetry_prod", "2026-06-19T11:06:54.186Z", "tomas eviltwin", "Spa", "porsche_991ii_gt3_r")
    assert "FROM ac_telemetry_prod" in sql
    assert "session_id = '2026-06-19T11:06:54.186Z'" in sql
    assert "driver = 'tomas eviltwin'" in sql
    assert "track = 'Spa'" in sql
    assert "carModel = 'porsche_991ii_gt3_r'" in sql
    assert "normalizedCarPosition AS pos" in sql


def test_build_session_sql_escapes_quote() -> None:
    sql = tv.build_session_sql("t", "s", "o'brien", "Spa", "car")
    assert "driver = 'o''brien'" in sql


def test_build_session_sql_rejects_bad_table() -> None:
    with pytest.raises(ValueError):
        tv.build_session_sql("t; DROP TABLE x", "s", "d", "Spa", "car")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/test-backend.sh tests/test_telemetry_viz.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement structures + helpers**

Create `shared/post_race_ai/telemetry_viz.py`:
```python
"""Post-race telemetry visualization: lake fetch → clean → one combined SVG.

Best-effort throughout; the orchestrator never raises. Cleaning rules and the
hotlap lap-1 staging trim are documented in the design spec
(docs/superpowers/specs/2026-06-21-post-race-telemetry-viz-design.md).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from api.models import Analysis, Test

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Lap:
    lap: int
    pos: list[float]
    speed: list[float]
    gas: list[float]
    brake: list[float]
    gear: list[float]
    lap_ms: int
    valid: bool


@dataclass
class LapSeries:
    laps: list[Lap] = field(default_factory=list)
    fastest_valid_idx: int | None = None


def format_lap_ms(ms: int) -> str:
    """Format a lap time in ms as m:ss.mmm (e.g. 144795 -> '2:24.795')."""
    minutes = ms // 60000
    seconds = (ms % 60000) / 1000
    return f"{minutes}:{seconds:06.3f}"


def build_session_sql(
    table: str, session_id: str, driver: str, track: str, car_model: str
) -> str:
    """Build the single per-session telemetry query (partition-equality, prunable)."""
    if not _IDENT_RE.match(table):
        raise ValueError(f"unsafe table identifier: {table!r}")

    def q(v: str) -> str:
        return v.replace("'", "''")

    return (
        "SELECT lap, normalizedCarPosition AS pos, speedKmh, gas, brake, gear, "
        "iCurrentTime, isValidLap, timestamp_ms "
        f"FROM {table} "
        f"WHERE session_id = '{q(session_id)}' AND driver = '{q(driver)}' "
        f"AND track = '{q(track)}' AND carModel = '{q(car_model)}'"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_telemetry_viz.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add shared/post_race_ai/telemetry_viz.py test-manager-backend/tests/test_telemetry_viz.py
git commit -m "feat(post-race): telemetry viz data structures + SQL builder"
```

---

### Task 4: Lap cleaning (`clean_laps`)

**Files:**
- Modify: `shared/post_race_ai/telemetry_viz.py`
- Test: `test-manager-backend/tests/test_telemetry_viz.py`

**Interfaces:**
- Consumes: `Lap`, `LapSeries` (Task 3).
- Produces: `clean_laps(df: pd.DataFrame, *, min_samples:int=1000, n_bins:int=400, invalid_tol:int=5) -> LapSeries`. Helpers `_trim_lap1_staging(g)`, `_downsample(g, n_bins)`.

- [ ] **Step 1: Add a synthetic-lap test helper + failing tests**

Append to `test-manager-backend/tests/test_telemetry_viz.py`:
```python
import numpy as np
import pandas as pd


def _lap_df(lap: int, n: int, *, pos_start: float = 0.0, pos_end: float = 1.0, invalid: int = 0, speed: float = 200.0, lap_ms: int = 100000) -> pd.DataFrame:
    """One synthetic lap: monotonic pos pos_start..pos_end, ict ramps to lap_ms."""
    pos = np.linspace(pos_start, pos_end, n)
    return pd.DataFrame(
        {
            "lap": lap,
            "pos": pos,
            "speedKmh": np.full(n, speed),
            "gas": np.full(n, 0.8),
            "brake": np.zeros(n),
            "gear": np.full(n, 4),
            "iCurrentTime": np.linspace(0, lap_ms, n).astype(int),
            "isValidLap": np.array([0 if i < invalid else 1 for i in range(n)]),
            "timestamp_ms": np.arange(n) * 20,
        }
    )


def test_clean_laps_drops_last_lap_and_sliver() -> None:
    df = pd.concat([
        _lap_df(1, 5000, lap_ms=100000),
        _lap_df(2, 5000, lap_ms=99000),
        _lap_df(3, 300),   # sliver (<=1000) — but also not last; dropped as sliver
        _lap_df(4, 200),   # last lap — dropped
    ], ignore_index=True)
    series = tv.clean_laps(df)
    kept = [lp.lap for lp in series.laps]
    assert kept == [1, 2]  # 3 sliver-dropped, 4 last-dropped


def test_clean_laps_fastest_valid() -> None:
    df = pd.concat([
        _lap_df(1, 5000, lap_ms=100000, invalid=0),  # valid, slower
        _lap_df(2, 5000, lap_ms=98000, invalid=3000),  # faster but INVALID
        _lap_df(3, 5000, lap_ms=99000, invalid=0),  # valid, fastest valid
        _lap_df(4, 200),  # last, dropped
    ], ignore_index=True)
    series = tv.clean_laps(df)
    assert series.fastest_valid_idx is not None
    assert series.laps[series.fastest_valid_idx].lap == 3


def test_clean_laps_no_valid_lap() -> None:
    df = pd.concat([
        _lap_df(1, 5000, invalid=4000),
        _lap_df(2, 5000, invalid=4000),
        _lap_df(3, 200),  # last
    ], ignore_index=True)
    series = tv.clean_laps(df)
    assert len(series.laps) == 2
    assert series.fastest_valid_idx is None


def test_clean_laps_trims_lap1_staging() -> None:
    # lap 1: staging 0.9->1.0 then wrap to 0.0->1.0 (non-monotonic in time)
    staging = _lap_df(1, 1500, pos_start=0.9, pos_end=1.0)
    flying = _lap_df(1, 5000, pos_start=0.0, pos_end=1.0)
    flying["timestamp_ms"] = flying["timestamp_ms"] + 100000  # later in time
    df = pd.concat([staging, flying, _lap_df(2, 200)], ignore_index=True)
    series = tv.clean_laps(df)
    assert len(series.laps) == 1
    # after trim+sort+downsample, pos is monotonic non-decreasing
    pos = series.laps[0].pos
    assert all(pos[i] <= pos[i + 1] + 1e-9 for i in range(len(pos) - 1))


def test_clean_laps_downsamples() -> None:
    df = pd.concat([_lap_df(1, 8000), _lap_df(2, 200)], ignore_index=True)
    series = tv.clean_laps(df, n_bins=400)
    assert 0 < len(series.laps[0].pos) <= 400


def test_clean_laps_empty() -> None:
    assert tv.clean_laps(pd.DataFrame()).laps == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/test-backend.sh tests/test_telemetry_viz.py -k clean_laps -v`
Expected: FAIL — `clean_laps` not defined.

- [ ] **Step 3: Implement `clean_laps` + helpers**

Append to `shared/post_race_ai/telemetry_viz.py`:
```python
def _trim_lap1_staging(g: pd.DataFrame) -> pd.DataFrame:
    """Drop the hotlap staging prefix of lap 1 (Telemetry Explorer wrap logic)."""
    g = g.sort_values("timestamp_ms").reset_index(drop=True)
    pos = g["pos"].to_numpy()
    for i in range(1, len(pos)):
        if pos[i - 1] > 0.9 and pos[i] < 0.1:
            return g.iloc[i:]
    if len(g) and g["pos"].min() > 0.1:
        return g.iloc[0:0]  # out-lap only, no full circuit
    return g


def _downsample(g: pd.DataFrame, n_bins: int) -> pd.DataFrame:
    """Bin pos into n_bins equal bins (0..1), mean per bin, drop empty bins."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins = pd.cut(g["pos"], bins=edges, include_lowest=True)
    cols = ["pos", "speed", "gas", "brake", "gear"]
    agg = g.groupby(bins, observed=True)[cols].mean().dropna(subset=["pos"])
    return agg.sort_values("pos").reset_index(drop=True)


def clean_laps(
    df: pd.DataFrame,
    *,
    min_samples: int = 1000,
    n_bins: int = 400,
    invalid_tol: int = 5,
) -> LapSeries:
    """Turn a raw per-session telemetry DataFrame into plot-ready per-lap series."""
    if df is None or df.empty:
        logger.info("[viz] clean_laps: empty input")
        return LapSeries()
    df = df.rename(columns={"speedKmh": "speed"})
    n_raw = len(df)
    df = df[
        df["speed"].between(0, 400)
        & df["gas"].between(0, 1)
        & df["brake"].between(0, 1)
        & df["gear"].between(0, 8)
    ]
    if len(df) < n_raw:
        logger.info("[viz] clean_laps: dropped %d out-of-range rows", n_raw - len(df))
    if df.empty:
        return LapSeries()

    all_laps = sorted(int(x) for x in df["lap"].unique())
    max_lap = max(all_laps)
    logger.info("[viz] clean_laps: laps=%s, dropping last lap %d", all_laps, max_lap)

    out: list[Lap] = []
    for lap in all_laps:
        if lap == max_lap:
            continue
        g = df[df["lap"] == lap]
        if len(g) <= min_samples:
            logger.info("[viz] clean_laps: drop sliver lap %d (n=%d)", lap, len(g))
            continue
        lap_ms = int(g["iCurrentTime"].max())
        n_invalid = int((g["isValidLap"] == 0).sum())
        valid = n_invalid <= invalid_tol
        if lap == 1:
            g = _trim_lap1_staging(g)
            if g.empty:
                logger.info("[viz] clean_laps: lap 1 out-lap only, dropped")
                continue
        binned = _downsample(g, n_bins)
        if binned.empty:
            logger.info("[viz] clean_laps: lap %d empty after downsample", lap)
            continue
        out.append(
            Lap(
                lap=lap,
                pos=binned["pos"].tolist(),
                speed=binned["speed"].tolist(),
                gas=binned["gas"].tolist(),
                brake=binned["brake"].tolist(),
                gear=binned["gear"].tolist(),
                lap_ms=lap_ms,
                valid=valid,
            )
        )

    valid_idx = [i for i, lp in enumerate(out) if lp.valid]
    fastest = min(valid_idx, key=lambda i: out[i].lap_ms) if valid_idx else None
    logger.info(
        "[viz] clean_laps: kept %d laps, fastest_valid_idx=%s", len(out), fastest
    )
    return LapSeries(laps=out, fastest_valid_idx=fastest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_telemetry_viz.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add shared/post_race_ai/telemetry_viz.py test-manager-backend/tests/test_telemetry_viz.py
git commit -m "feat(post-race): clean_laps — drop last/sliver, lap-1 trim, downsample, fastest valid"
```

---

### Task 5: SVG renderer (`render_telemetry_svg`)

**Files:**
- Modify: `shared/post_race_ai/telemetry_viz.py`
- Test: `test-manager-backend/tests/test_telemetry_viz.py`

**Interfaces:**
- Consumes: `LapSeries`, `Lap`, `format_lap_ms`.
- Produces: `render_telemetry_svg(series: LapSeries) -> str | None`. Returns an SVG string, or `None` when there is nothing to plot.

- [ ] **Step 1: Write the failing tests**

Append to `test-manager-backend/tests/test_telemetry_viz.py`:
```python
def test_render_none_when_empty() -> None:
    assert tv.render_telemetry_svg(tv.LapSeries()) is None


def test_render_returns_svg_with_fastest() -> None:
    df = pd.concat([_lap_df(1, 5000, lap_ms=100000), _lap_df(2, 5000, lap_ms=99000), _lap_df(3, 200)], ignore_index=True)
    series = tv.clean_laps(df)
    svg = tv.render_telemetry_svg(series)
    assert svg is not None
    assert svg.lstrip().startswith("<?xml") or "<svg" in svg
    assert "</svg>" in svg


def test_render_returns_svg_without_valid_lap() -> None:
    df = pd.concat([_lap_df(1, 5000, invalid=4000), _lap_df(2, 5000, invalid=4000), _lap_df(3, 200)], ignore_index=True)
    series = tv.clean_laps(df)
    svg = tv.render_telemetry_svg(series)
    assert svg is not None and "<svg" in svg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/test-backend.sh tests/test_telemetry_viz.py -k render -v`
Expected: FAIL — `render_telemetry_svg` not defined.

- [ ] **Step 3: Implement the renderer**

Append to `shared/post_race_ai/telemetry_viz.py`:
```python
# Quix palette
_LAP_COLORS = ["#0064ff", "#ff7828", "#00b3a4", "#9b51e0", "#e0218a", "#434352"]
_VALID_BAR = "#0064ff"
_INVALID_BAR = "#b8bdc9"
_FASTEST_BAR = "#ff7828"


def render_telemetry_svg(series: LapSeries) -> str | None:
    """Render the combined telemetry figure to one SVG string, or None if empty."""
    if not series.laps:
        return None

    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    has_fastest = series.fastest_valid_idx is not None
    rows: list[tuple[str, int]] = [("speed", 3)]
    if has_fastest:
        rows += [("pedals", 2), ("gear", 1)]
    rows.append(("laptimes", 2))

    fig = plt.figure(figsize=(7.0, 9.5))
    gs = GridSpec(len(rows), 1, height_ratios=[h for _, h in rows], hspace=0.5)
    axes = {key: fig.add_subplot(gs[i]) for i, (key, _) in enumerate(rows)}

    # 1. Speed — all laps
    ax = axes["speed"]
    for i, lp in enumerate(series.laps):
        ax.plot(
            lp.pos,
            lp.speed,
            color=_LAP_COLORS[i % len(_LAP_COLORS)],
            linewidth=1.0,
            label=f"L{lp.lap} · {format_lap_ms(lp.lap_ms)}",
        )
    ax.set_title("Speed — all laps", fontsize=9, loc="left")
    ax.set_ylabel("km/h", fontsize=8)
    ax.set_xlim(0, 1)
    ax.legend(fontsize=6, loc="lower center", ncol=min(len(series.laps), 4))
    if not has_fastest:
        ax.set_xlabel("Track position", fontsize=8)

    if has_fastest:
        fast = series.laps[series.fastest_valid_idx]
        ax = axes["pedals"]
        ax.plot(fast.pos, fast.gas, color="#00b3a4", linewidth=1.0, label="Throttle")
        ax.plot(fast.pos, fast.brake, color="#d12d2d", linewidth=1.0, label="Brake")
        ax.set_title(
            f"Throttle & brake — fastest lap (L{fast.lap}, {format_lap_ms(fast.lap_ms)})",
            fontsize=9,
            loc="left",
        )
        ax.set_ylabel("0–1", fontsize=8)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.05)
        ax.legend(fontsize=6, loc="upper right")

        ax = axes["gear"]
        ax.step(fast.pos, fast.gear, color="#0a0b24", linewidth=1.0, where="post")
        ax.set_title("Gear — fastest lap", fontsize=9, loc="left")
        ax.set_ylabel("gear", fontsize=8)
        ax.set_xlim(0, 1)
        ax.set_xlabel("Track position", fontsize=8)

    # 4. Lap times
    ax = axes["laptimes"]
    xs = list(range(len(series.laps)))
    colors = [
        _FASTEST_BAR
        if i == series.fastest_valid_idx
        else (_VALID_BAR if lp.valid else _INVALID_BAR)
        for i, lp in enumerate(series.laps)
    ]
    ax.bar(xs, [lp.lap_ms / 1000 for lp in series.laps], color=colors)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"L{lp.lap}" for lp in series.laps], fontsize=7)
    ax.set_title("Lap times (orange=fastest, grey=invalid)", fontsize=9, loc="left")
    ax.set_ylabel("seconds", fontsize=8)

    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_telemetry_viz.py -k render -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add shared/post_race_ai/telemetry_viz.py test-manager-backend/tests/test_telemetry_viz.py
git commit -m "feat(post-race): render combined telemetry SVG (4 conditional subplots)"
```

---

### Task 6: Context resolver + orchestrator

**Files:**
- Modify: `shared/post_race_ai/telemetry_viz.py`
- Test: `test-manager-backend/tests/test_telemetry_viz.py`

**Interfaces:**
- Consumes: `build_session_sql`, `clean_laps`, `render_telemetry_svg`; `lake_query` from `lake.py`; `Analysis`/`Test` models (duck-typed).
- Produces:
  - `resolve_lake_keys(analysis, test) -> tuple[str, str, str] | None` → `(driver_lower, track, car_model)`.
  - `build_analysis_telemetry_svg(analysis, test, table: str) -> str | None` (best-effort, never raises).

- [ ] **Step 1: Write the failing tests**

Append to `test-manager-backend/tests/test_telemetry_viz.py`:
```python
from api.models import Analysis, SessionInfo, Test


def _analysis(session_id="2026-06-19T11:06:54.186Z", driver="Tomas Eviltwin", status="complete") -> Analysis:
    from api.models import AnalysisContext

    return Analysis(
        _id="a1",
        test_id="TST-0001",
        session_id=session_id,
        status=status,
        context=AnalysisContext(driver=driver, track="Spa", car_model="porsche_991ii_gt3_r"),
    )


def _test_with_session(session_id="2026-06-19T11:06:54.186Z") -> Test:
    return Test(
        _id="TST-0001",
        name="t",
        driver="Tomas Eviltwin",
        test_rig_device_id="DEV-0001",
        environment_id="ENV-0001",
        experiment_id="ConferenceBrno",
        sessions=[SessionInfo(session_id=session_id, track="Spa", car_model="porsche_991ii_gt3_r")],
    )


def test_resolve_lake_keys_lowercases_driver() -> None:
    keys = tv.resolve_lake_keys(_analysis(), _test_with_session())
    assert keys == ("tomas eviltwin", "Spa", "porsche_991ii_gt3_r")


def test_resolve_lake_keys_none_without_session() -> None:
    assert tv.resolve_lake_keys(_analysis(session_id=None), _test_with_session()) is None


def test_build_svg_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.concat([_lap_df(1, 5000), _lap_df(2, 5000, lap_ms=99000), _lap_df(3, 200)], ignore_index=True)
    monkeypatch.setattr(tv, "lake_query", lambda sql: df)
    svg = tv.build_analysis_telemetry_svg(_analysis(), _test_with_session(), "ac_telemetry_prod")
    assert svg is not None and "<svg" in svg


def test_build_svg_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(sql):  # noqa: ANN001, ANN202
        raise RuntimeError("no creds")

    monkeypatch.setattr(tv, "lake_query", boom)
    assert tv.build_analysis_telemetry_svg(_analysis(), _test_with_session(), "t") is None


def test_build_svg_none_for_test_wide() -> None:
    assert tv.build_analysis_telemetry_svg(_analysis(session_id=None), _test_with_session(), "t") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/test-backend.sh tests/test_telemetry_viz.py -k "resolve or build_svg" -v`
Expected: FAIL — names not defined.

- [ ] **Step 3: Implement resolver + orchestrator**

Add the import near the top of `shared/post_race_ai/telemetry_viz.py` (after the existing imports):
```python
from .lake import lake_query
```

Append to `shared/post_race_ai/telemetry_viz.py`:
```python
def resolve_lake_keys(analysis: "Analysis", test: "Test") -> tuple[str, str, str] | None:
    """Resolve (driver_lowercased, track, car_model) for the lake query, or None.

    Session-level only. driver from context/extra (lowercased to match the lake
    partition); track + car_model prefer the matching SessionInfo (authoritative).
    """
    if not analysis.session_id:
        return None
    ctx = analysis.context
    extra = analysis.extra or {}
    driver = (ctx.driver if ctx else None) or extra.get("driver")
    track = (ctx.track if ctx else None) or extra.get("track")
    car = (ctx.car_model if ctx else None) or extra.get("car_model")
    for s in test.sessions:
        if s.session_id == analysis.session_id:
            track = s.track or track
            car = s.car_model or car
            break
    if not (driver and track and car):
        logger.info(
            "[viz] resolve_lake_keys: incomplete (driver=%s track=%s car=%s)",
            driver,
            track,
            car,
        )
        return None
    return driver.lower(), track, car


def build_analysis_telemetry_svg(
    analysis: "Analysis", test: "Test", table: str
) -> str | None:
    """Best-effort: resolve keys, query the lake, clean, render. Never raises."""
    try:
        keys = resolve_lake_keys(analysis, test)
        if keys is None:
            return None
        driver, track, car = keys
        sql = build_session_sql(table, analysis.session_id, driver, track, car)
        df = lake_query(sql)
        series = clean_laps(df)
        svg = render_telemetry_svg(series)
        if svg is None:
            logger.info("[viz] no telemetry plots for analysis %s", analysis.id)
        return svg
    except Exception:
        logger.warning(
            "[viz] telemetry build failed for analysis %s",
            getattr(analysis, "id", "?"),
            exc_info=True,
        )
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_telemetry_viz.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add shared/post_race_ai/telemetry_viz.py test-manager-backend/tests/test_telemetry_viz.py
git commit -m "feat(post-race): telemetry context resolver + best-effort orchestrator"
```

---

### Task 7: PDF integration (Telemetry section)

**Files:**
- Modify: `shared/post_race_ai/pdf.py` (CSS block ~19-40; doc assembly ~184-196; `render_analysis_pdf` ~159)
- Test: `test-manager-backend/tests/test_pdf.py`

**Interfaces:**
- Consumes: a telemetry SVG string (built by callers in Tasks 8–10).
- Produces: `render_analysis_pdf(analysis, telemetry_svg: str | None = None) -> bytes`; `_telemetry_section(svg: str | None) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `test-manager-backend/tests/test_pdf.py`:
```python
from shared.post_race_ai.pdf import _telemetry_section


def test_telemetry_section_empty() -> None:
    assert _telemetry_section(None) == ""
    assert _telemetry_section("") == ""


def test_telemetry_section_embeds_svg() -> None:
    html = _telemetry_section("<svg></svg>")
    assert "data:image/svg+xml;base64," in html
    assert "Telemetry" in html
```

And (renders a real PDF — needs WeasyPrint libs):
```python
@pytest.mark.requires_weasyprint
def test_render_includes_telemetry_when_svg_passed() -> None:
    pdf = render_analysis_pdf(_complete_analysis(), telemetry_svg="<svg width='10' height='10'></svg>")
    assert pdf[:4] == b"%PDF"
```
(`_complete_analysis` is the existing helper in this test file; reuse it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/test-backend.sh tests/test_pdf.py -k telemetry -v`
Expected: FAIL — `_telemetry_section` not defined / signature mismatch.

- [ ] **Step 3: Add CSS**

In `shared/post_race_ai/pdf.py`, inside the `_CSS` string (block at lines 19-40), append these rules before the closing of the CSS string:
```css
.telemetry { break-before: page; }
.telemetry-fig { width: 100%; height: auto; break-inside: avoid; }
```

- [ ] **Step 4: Add `_telemetry_section`**

Add to `shared/post_race_ai/pdf.py` (near the other `_*_table` helpers, ~line 142):
```python
def _telemetry_section(svg: str | None) -> str:
    """Render the deterministic telemetry figure into a page-broken section, or ''."""
    if not svg:
        return ""
    import base64

    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (
        '<section class="telemetry"><h2>Telemetry</h2>'
        f'<img class="telemetry-fig" src="data:image/svg+xml;base64,{b64}" />'
        "</section>"
    )
```

- [ ] **Step 5: Thread the param + embed**

Change the signature at line 159:
```python
def render_analysis_pdf(analysis: Analysis, telemetry_svg: str | None = None) -> bytes:
```
In the doc assembly (after `{_anomalies_table(analysis)}`, line 194), add:
```python
{_telemetry_section(telemetry_svg)}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_pdf.py -v`
Expected: pass (telemetry render test runs if WeasyPrint libs present, else skips).

- [ ] **Step 7: Commit**

```bash
git add shared/post_race_ai/pdf.py test-manager-backend/tests/test_pdf.py
git commit -m "feat(post-race): embed telemetry SVG section in PDF (own page)"
```

---

### Task 8: Wire `/pdf` endpoint

**Files:**
- Modify: `test-manager-backend/api/routes/analyses.py` (imports ~1-35; `get_analysis_pdf` ~255-278)
- Test: `test-manager-backend/tests/test_analyses.py`

**Interfaces:**
- Consumes: `build_analysis_telemetry_svg` (Task 6), `render_analysis_pdf(..., telemetry_svg=...)` (Task 7).

- [ ] **Step 1: Write the failing test**

Append to `test-manager-backend/tests/test_analyses.py`:
```python
@pytest.mark.requires_weasyprint
def test_pdf_includes_telemetry(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.routes import analyses as analyses_route

    monkeypatch.setattr(
        analyses_route, "build_analysis_telemetry_svg", lambda a, t, table: "<svg width='10' height='10'></svg>"
    )
    analysis_id = _insert_analysis(status="complete")
    # ensure a test doc exists so the route loads it
    from api.mongo import get_mongo

    get_mongo().tests.insert_one({"_id": "TST-0001", "name": "t", "driver": "d", "test_rig_device_id": "DEV-0001", "environment_id": "ENV-0001", "experiment_id": "x", "sessions": []})
    resp = client.get(f"/api/v1/analyses/{analysis_id}/pdf")
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == b"%PDF"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/test-backend.sh tests/test_analyses.py -k test_pdf_includes_telemetry -v`
Expected: FAIL — `build_analysis_telemetry_svg` not importable in the route module.

- [ ] **Step 3: Implement the wiring**

In `test-manager-backend/api/routes/analyses.py`, add imports (near line 15):
```python
from shared.post_race_ai.telemetry_viz import build_analysis_telemetry_svg
```
Ensure `Test` and `get_settings` are imported:
```python
from ..models import Analysis, Test  # add Test to existing import
from ..settings import get_settings
```
In `get_analysis_pdf`, replace `pdf = render_analysis_pdf(analysis)` (line 271) with:
```python
    telemetry_svg = None
    test_doc = mongo.tests.find_one({"_id": analysis.test_id})
    if test_doc:
        telemetry_svg = build_analysis_telemetry_svg(
            analysis, Test(**test_doc), get_settings().telemetry_table_name
        )
    pdf = render_analysis_pdf(analysis, telemetry_svg=telemetry_svg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_analyses.py -k pdf -v`
Expected: pass (existing pdf tests + new one).

- [ ] **Step 5: Commit**

```bash
git add test-manager-backend/api/routes/analyses.py test-manager-backend/tests/test_analyses.py
git commit -m "feat(post-race): build telemetry SVG into the /pdf endpoint"
```

---

### Task 9: `/telemetry` JSON endpoint

**Files:**
- Modify: `test-manager-backend/api/routes/analyses.py`
- Test: `test-manager-backend/tests/test_analyses.py`

**Interfaces:**
- Produces: `GET /api/v1/analyses/{analysis_id}/telemetry` → `{"svg": str | None}`.

- [ ] **Step 1: Write the failing tests**

Append to `test-manager-backend/tests/test_analyses.py`:
```python
def test_telemetry_endpoint_404(client: TestClient) -> None:
    assert client.get("/api/v1/analyses/nope/telemetry").status_code == 404


def test_telemetry_endpoint_null_when_incomplete(client: TestClient) -> None:
    analysis_id = _insert_analysis(status="running", analysis_id="a-tel-run")
    resp = client.get(f"/api/v1/analyses/{analysis_id}/telemetry")
    assert resp.status_code == 200
    assert resp.json() == {"svg": None}


def test_telemetry_endpoint_returns_svg(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.mongo import get_mongo
    from api.routes import analyses as analyses_route

    monkeypatch.setattr(analyses_route, "build_analysis_telemetry_svg", lambda a, t, table: "<svg/>")
    analysis_id = _insert_analysis(status="complete", analysis_id="a-tel-ok")
    get_mongo().tests.insert_one({"_id": "TST-0001", "name": "t", "driver": "d", "test_rig_device_id": "DEV-0001", "environment_id": "ENV-0001", "experiment_id": "x", "sessions": []})
    resp = client.get(f"/api/v1/analyses/{analysis_id}/telemetry")
    assert resp.status_code == 200
    assert resp.json() == {"svg": "<svg/>"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash scripts/test-backend.sh tests/test_analyses.py -k telemetry_endpoint -v`
Expected: FAIL — 404 route not found for the new path.

- [ ] **Step 3: Implement the endpoint**

Add to `test-manager-backend/api/routes/analyses.py` (after `get_analysis_pdf`):
```python
@router.get("/analyses/{analysis_id}/telemetry")
def get_analysis_telemetry(
    analysis_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> dict[str, str | None]:
    """Telemetry figure SVG for a completed session analysis (best-effort).

    {"svg": "<svg...>"} when available; {"svg": null} when there is nothing to
    show (incomplete, test-wide, no lake creds, no usable laps, or any error).
    """
    doc = mongo.analyses.find_one({"_id": analysis_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")
    analysis = Analysis(**doc)
    if analysis.status != "complete":
        return {"svg": None}
    test_doc = mongo.tests.find_one({"_id": analysis.test_id})
    if not test_doc:
        return {"svg": None}
    svg = build_analysis_telemetry_svg(
        analysis, Test(**test_doc), get_settings().telemetry_table_name
    )
    logger.info("[analyses] GET %s/telemetry -> %s", analysis_id, "svg" if svg else "none")
    return {"svg": svg}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_analyses.py -k telemetry_endpoint -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add test-manager-backend/api/routes/analyses.py test-manager-backend/tests/test_analyses.py
git commit -m "feat(post-race): add GET /analyses/{id}/telemetry SVG endpoint"
```

---

### Task 10: Wire telemetry into the auto-email PDF

**Files:**
- Modify: `test-manager-backend/api/notify.py` (`send_analysis_email` ~50-75)
- Test: `test-manager-backend/tests/test_email_notify.py` (fix the `render_analysis_pdf` monkeypatch signature at line 80)

**Interfaces:**
- Consumes: `build_analysis_telemetry_svg`, `render_analysis_pdf(..., telemetry_svg=...)`.

- [ ] **Step 1: Update the existing test's monkeypatch + add a guard**

In `test-manager-backend/tests/test_email_notify.py`, change line 80 from:
```python
    monkeypatch.setattr(notify, "render_analysis_pdf", lambda a: b"PDFBYTES")
```
to:
```python
    monkeypatch.setattr(notify, "render_analysis_pdf", lambda a, telemetry_svg=None: b"PDFBYTES")
    monkeypatch.setattr(notify, "build_analysis_telemetry_svg", lambda a, t, table: None)
```

- [ ] **Step 2: Run the email tests to verify the relevant one fails**

Run: `bash scripts/test-backend.sh tests/test_email_notify.py -v`
Expected: the auto-email test FAILS (notify calls `render_analysis_pdf(analysis, telemetry_svg=...)` once wired; the lambda is fixed but `build_analysis_telemetry_svg` isn't imported in notify yet → AttributeError on monkeypatch). This confirms the wiring point.

- [ ] **Step 3: Implement the wiring**

In `test-manager-backend/api/notify.py`, add imports (near line 14-18):
```python
from shared.post_race_ai.telemetry_viz import build_analysis_telemetry_svg

from .models import Analysis, Test  # add Test
from .settings import get_settings
```
In `send_analysis_email`, replace `pdf = render_analysis_pdf(analysis)` (line 63) with:
```python
    test_doc = mongo.tests.find_one({"_id": analysis.test_id})
    telemetry_svg = (
        build_analysis_telemetry_svg(
            analysis, Test(**test_doc), get_settings().telemetry_table_name
        )
        if test_doc
        else None
    )
    pdf = render_analysis_pdf(analysis, telemetry_svg=telemetry_svg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/test-backend.sh tests/test_email_notify.py -v`
Expected: all pass.

- [ ] **Step 5: Full backend gate**

Run: `bash scripts/test-backend.sh`
Then: `docker exec ac-quix-backend sh -c "cd /app && uv run ty check"` and `UV_PROJECT_ENVIRONMENT=/tmp/tm-test-venv uv run --project test-manager-backend ruff check shared test-manager-backend`
Expected: tests green; ty == 7 pre-existing baseline; ruff clean for new files.

- [ ] **Step 6: Commit**

```bash
git add test-manager-backend/api/notify.py test-manager-backend/tests/test_email_notify.py
git commit -m "feat(post-race): attach telemetry plots to the auto-email PDF"
```

---

### Task 11: Frontend — Telemetry section on the card

**Files:**
- Modify: `test-manager-frontend/lib/api/analyses.ts` (add `getTelemetry`)
- Modify: the `useAnalysesApi` hook (same place getPdf is exposed — find with `grep -rn "useAnalysesApi" test-manager-frontend`)
- Create: `test-manager-frontend/app/analysis/ai-summary/components/telemetry-section.tsx`
- Modify: `test-manager-frontend/app/analysis/ai-summary/components/analysis-card.tsx` (add the section after the summary block, ~line 465)
- Test: `test-manager-frontend/lib/api/__tests__/analyses.telemetry.test.ts` (vitest)

**Interfaces:**
- Consumes: `apiGet<T>(endpoint, params?, token?, refreshToken?)` from `lib/api/client.ts`.
- Produces: `analysesApi.getTelemetry(id, token?, refreshToken?) -> Promise<{svg: string | null}>`; `<TelemetrySection analysisId status />`.

- [ ] **Step 1: Write the failing api test**

Create `test-manager-frontend/lib/api/__tests__/analyses.telemetry.test.ts`:
```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";
import { analysesApi } from "../analyses";

describe("analysesApi.getTelemetry", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("GETs the telemetry endpoint and returns the svg payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ svg: "<svg/>" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await analysesApi.getTelemetry("a1", "tok");
    expect(fetchMock).toHaveBeenCalled();
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/analyses/a1/telemetry");
    expect(result).toEqual({ svg: "<svg/>" });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run (in a worktree or with the dev server stopped): `cd test-manager-frontend && npm test -- analyses.telemetry`
Expected: FAIL — `getTelemetry` is not a function.

- [ ] **Step 3: Add `getTelemetry` to the api object**

In `test-manager-frontend/lib/api/analyses.ts`, add to the `analysesApi` object (mirror `getRecipient`, after `getPdf`):
```typescript
  /**
   * Fetch the deterministic telemetry figure (SVG) for a completed session
   * analysis. {svg: null} when there is nothing to show.
   */
  getTelemetry: (
    analysisId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<{ svg: string | null }>(
      `/analyses/${analysisId}/telemetry`,
      undefined,
      token,
      refreshToken,
    );
  },
```

- [ ] **Step 4: Run the api test to verify it passes**

Run: `cd test-manager-frontend && npm test -- analyses.telemetry`
Expected: pass.

- [ ] **Step 5: Expose via the `useAnalysesApi` hook**

Find the hook (`grep -rn "getPdf" test-manager-frontend/lib`/`hooks`). Mirror the `getPdf` binding for `getTelemetry` so it auto-injects the token (same pattern, one line).

- [ ] **Step 6: Create the section component**

Create `test-manager-frontend/app/analysis/ai-summary/components/telemetry-section.tsx`:
```typescript
"use client";

import { useEffect, useState } from "react";
import { useAnalysesApi } from "@/lib/api/analyses";
import { SectionHeading } from "./section-heading"; // adjust import to the existing SectionHeading location

export function TelemetrySection({
  analysisId,
  status,
}: {
  analysisId: string;
  status: string;
}) {
  const analysesApi = useAnalysesApi();
  const [svg, setSvg] = useState<string | null>(null);

  useEffect(() => {
    if (status !== "complete") return;
    let cancelled = false;
    analysesApi
      .getTelemetry(analysisId)
      .then((r) => {
        if (!cancelled) setSvg(r.svg);
      })
      .catch(() => {
        if (!cancelled) setSvg(null);
      });
    return () => {
      cancelled = true;
    };
  }, [analysisId, status]);

  if (!svg) return null;

  const dataUri = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
  return (
    <section>
      <SectionHeading>Telemetry</SectionHeading>
      <img src={dataUri} alt="Lap telemetry" style={{ maxWidth: "100%", height: "auto" }} />
    </section>
  );
}
```
(Adjust the `SectionHeading` import to wherever `analysis-card.tsx` imports it from.)

- [ ] **Step 7: Mount it in the card**

In `test-manager-frontend/app/analysis/ai-summary/components/analysis-card.tsx`, import and render `<TelemetrySection analysisId={analysis.id} status={analysis.status} />` at the bottom, after the summary `<section>` (~line 465).

- [ ] **Step 8: Frontend gates**

Run (in a worktree): `cd test-manager-frontend && npm run type-check && npm run lint && npx prettier --check "app/analysis/ai-summary/components/telemetry-section.tsx" "lib/api/analyses.ts" && npm run build`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add test-manager-frontend/lib/api/analyses.ts test-manager-frontend/app/analysis/ai-summary/components/telemetry-section.tsx test-manager-frontend/app/analysis/ai-summary/components/analysis-card.tsx test-manager-frontend/lib/api/__tests__/analyses.telemetry.test.ts
# plus the hook file modified in Step 5
git commit -m "feat(tm-frontend): telemetry plots section on the post-race card"
```

---

### Task 12: Deploy verification checklist (no code)

**Files:** none (operational).

- [ ] **Step 1:** Confirm `Quix__Lakehouse__Query__Url`/`__AuthToken` are injected into the deployed `test-manager-backend` after Task 1's blob bind. Check on byox (prod) AND dev. If dev does not inject, viz simply omits on dev — acceptable (prod = byox).
- [ ] **Step 2:** Confirm the deploy image rebuild picks up pandas+matplotlib (cloud builds from the dockerfile on deploy — no apt libs needed for matplotlib Agg).
- [ ] **Step 3:** Confirm `TABLE_NAME` (`AC_TELEMETRY_TABLE_NAME`) per-env points at the right table (`ac_telemetry_prod` for prod) — already wired for the "View Data" embed; reused here.
- [ ] **Step 4:** Smoke-test: open a completed session analysis on the card → Telemetry section renders; download its PDF → Telemetry on its own last page; trigger an auto-email → attached PDF has the plots.

---

## Self-Review

**Spec coverage:**
- Lake client (2 vars, no fallback, call-time raise) → Task 2. ✓
- One SQL, partition-equality → Task 3. ✓
- Cleaning (drop last/sliver/lap-1 trim/sanity/downsample/valid/fastest) → Task 4. ✓
- One combined SVG, conditional subplots, A4-fit → Task 5. ✓
- Session-level only, best-effort orchestrator → Task 6. ✓
- PDF section + page-break CSS → Task 7. ✓
- `/pdf` + `/telemetry` + auto-email wiring → Tasks 8, 9, 10. ✓
- Frontend static SVG section (no chart lib) → Task 11. ✓
- Cloud blob bind for cred injection → Task 1. ✓
- Deploy verification → Task 12. ✓

**Placeholder scan:** Frontend Step 5 (hook) + the `SectionHeading` import path are intentionally "find the existing pattern" — both are one-line mirrors of existing code and exact paths can't be asserted without reading the hook file at execution time. Every backend step has complete code.

**Type consistency:** `LapSeries`/`Lap` field names, `build_analysis_telemetry_svg(analysis, test, table)` arity, and `render_analysis_pdf(analysis, telemetry_svg=None)` signature are consistent across Tasks 4–10. `{"svg": str | None}` response shape matches between Task 9 and Task 11.
