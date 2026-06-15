# Architecture: track-viewer

## What it does

`track-viewer/` is a small read-only FastAPI service that lists the track
layouts imported into MongoDB (`test_manager.track_layouts`, written by the
`track-importer` Job) and renders each layout's track map in a browser. It is
the "did the import work?" verification signal: if a layout shows up in the
sidebar and draws a coherent shape, the imported geometry is present and
loadable. It never writes to Mongo.

## Why this architecture

- **Mirror the existing stack, not invent one.** The app reuses
  `test-manager-backend` / `track-importer` conventions verbatim:
  `MongoSettings(env_prefix="MONGO_")` with a computed `url`, the same
  `MongoClient(...)` kwargs (`serverSelectionTimeoutMS=5000`), a `python:3.13-slim`
  + `uv sync --frozen` dockerfile, and an `app.yaml` declaring `MONGO_*`. This
  keeps the secret wiring and deployment shape identical to its siblings, so
  there is one Mongo-connection pattern in the repo, not three.
- **Lazy connect, never crash on unreachable Mongo.** `MongoClient`
  construction opens no socket, so a bad `MONGO_HOST` cannot take the process
  down at startup. Every Mongo operation is wrapped; failures return a
  structured `503 {error: ...}` (API) or a 503 body with `mongo_ok:false`
  (`/healthz`). The UI turns those into a banner. Trade-off: the first request
  after a Mongo outage pays the 5 s server-selection timeout — acceptable for
  a verification tool, and it is why the timeout is capped at 5 s.
- **Match the real importer doc schema, read only what the renderer needs.**
  The geometry endpoint reads the importer's actual point field names — `x`,
  `z`, `radius_m` (see `track-importer/importer.py:23-35` `_FLOAT_COLS` and
  `:53-60` where each point is built as a named-field dict) — and emits only
  those three per point as `[x, z, radius_m]` to keep payloads small. `_id` is
  the literal `"<track>/<layout>"` string (`importer.py:130`), so geometry
  lookup matches `_id` directly rather than reconstructing a query.
- **Canvas, not a chart library.** A track map is a per-segment-coloured
  polyline at equal aspect ratio with a start marker. Raw `<canvas>` does
  exactly that and redraws fast on resize; Chart.js/Plotly add weight and
  fight equal-aspect geometric plots. Bootstrap 5 (CDN) supplies the
  responsive sidebar/badges with no build step.
- **Relative asset/API URLs.** The public ingress may rewrite a path prefix
  (`/track-viewer/`). `index.html` references `./static/app.js` and `app.js`
  fetches `api/tracks` (no leading slash), so the UI works under any prefix.

## Data flow

```
browser  GET /                      -> static/index.html
         GET ./static/app.js        -> static/app.js
         GET api/tracks             -> api.list_tracks
                                        find({}, projection excl. points/corners)
                                        .sort(track, layout)            -> [summaries]
         click row
         GET api/tracks/<t>/<l>/geometry -> api.geometry
                                        find_one({_id: "<t>/<l>"})
                                        _downsample(points, max=3000)   (keep first+last)
                                        emit [[x, z, radius_m], ...]
                                        + corners passthrough           -> {geometry}
         GET /healthz               -> ping + count_documents({})       -> {mongo_ok, ...}
```

Mongo (`test_manager.track_layouts`) is the only data source. On any Mongo
error every endpoint returns a structured 503; `app.js` renders a "Cannot
reach MongoDB" banner. An empty collection returns `[]` (200) and the UI shows
"No tracks found … the import may not have run."

Downsampling: if `n_points > max_points` (3000), uniformly stride-sample with
`step = ceil(n / 3000)`, always appending the last point so the loop closes;
the response carries `downsampled: true` and `n_points_returned`.

Orientation: the renderer plots raw `x` (horizontal) vs `z` (vertical) at
equal aspect ratio and does NOT auto-north. Layouts may appear rotated vs a
real-world map — expected, not a bug (noted in the UI and `api.py` docstring).

## File inventory

| File | Purpose |
|------|---------|
| `track-viewer/main.py` | uvicorn bootstrap (trimmed copy of backend `main.py`), stdout log config. |
| `track-viewer/app/__init__.py` | Package marker. |
| `track-viewer/app/settings.py` | `MongoSettings` (env_prefix `MONGO_`, verbatim from siblings) + `ViewerSettings` (host/port/collection/max_points). |
| `track-viewer/app/mongo.py` | Read-only Mongo access: lazy `connect()` / `get_mongo()` / `disconnect()`. No `create_index`, no writes. |
| `track-viewer/app/api.py` | FastAPI app factory + routes: `/`, `/static`, `/healthz`, `/api/tracks`, `/api/tracks/{id:path}/geometry`. |
| `track-viewer/static/index.html` | Single-page UI (Bootstrap 5 CDN), responsive sidebar + canvas + legend. |
| `track-viewer/static/app.js` | Fetch logic + canvas renderer (radius→colour polyline, start marker, equal aspect). |
| `track-viewer/pyproject.toml` | Deps: fastapi, uvicorn[standard], pymongo, pydantic, pydantic-settings. |
| `track-viewer/uv.lock` | Frozen lockfile (dockerfile uses `uv sync --frozen`). |
| `track-viewer/dockerfile` | `python:3.13-slim` + uv, EXPOSE 8080, `uv run -m main`. |
| `track-viewer/app.yaml` | Quix app descriptor: MONGO_USER/PASSWORD (Secret), MONGO_HOST/PORT/DATABASE (FreeText). |
| `quix.yaml` (modified, additive) | New `track-viewer` Service deployment with public ingress + MONGO_* wiring. |

## Integration points

- **Upstream:** `track-importer` Job populates `test_manager.track_layouts`.
  The viewer is read-only against the same collection and depends on the
  importer's doc schema (`_id`, `track`, `trackConfiguration`, `layout`,
  `length_m`, `n_points`, `n_corners`, `points[]`, `corners[]`).
- **Mongo:** in-cluster service `mongodb:27017`. The viewer reuses the
  `MONGO_USER` / `MONGO_ROOT_PASSWORD` project secrets (same keys as
  `test-manager-backend` and `track-importer`).
- **Deployment:** public ingress (`urlPrefix: track-viewer`), 1 replica,
  cpu 200 / memory 400. Browser-reachable; talks to `mongodb` internally.

## Caveats

- **DB-name caveat.** Two DBs exist: `test_manager` (importer target, backend)
  and `ac_telemetry` (DCM). The viewer defaults `MONGO_DATABASE=test_manager`
  and stays overridable. If the UI shows "no tracks found" but the import
  reportedly succeeded, check for a DB-name mismatch first.
- The first request after a Mongo outage pays the 5 s server-selection
  timeout before the graceful 503.
