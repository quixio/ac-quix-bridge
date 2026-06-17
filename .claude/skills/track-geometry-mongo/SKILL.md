---
name: track-geometry-mongo
description: How the Telemetry Explorer (telemetry-comparison) loads track geometry + corner data from MongoDB track_layouts, driven by the track dropdown, with a bundled-CSV fallback. Use when modifying track map rendering, the /api/track or /api/track/layouts endpoints, the track/layout dropdowns, or debugging why the map shows the wrong (or fallback) circuit.
---

# Track geometry from MongoDB (Telemetry Explorer)

The track MAP in the Explorer is **orthogonal to telemetry** — it never hits the
lake. It comes from MongoDB `track_layouts` (written by the `track-importer`
Job), selected by the track dropdown, with a bundled CSV as the offline fallback.

## Pipeline (end to end)

```
track dropdown value (from the LAKE session selection)
  → app.js getActiveTrack() / refreshTrackForActive(track)
  → data.js fetchLayouts(track)  → GET /api/track/layouts?track=…   (Mongo)
  → data.js fetchTrack(track, layout) → GET /api/track?track=…&layout=…
  → track_loader._resolve_mongo_doc(track, layout)  → Mongo track_layouts
  → track_loader._transform_mongo_doc(doc)  → /api/track contract
  → (no doc / Mongo down) → _load_track_csv(DEFAULT_TRACK_CSV)  → same contract
```

## Files

- `telemetry-comparison/track_loader.py` — the endpoints + Mongo→contract transform.
  - `_resolve_mongo_doc(track, layout)`: `layout` present → `find_one({_id: "<track>/<layout>"})`; absent → `find({track}).sort(layout).limit(1)`.
  - `/api/track` (`get_track`): Mongo precedence → CSV fallback (`config.DEFAULT_TRACK_CSV`). Provenance in `track_file`: `mongo:<_id>` vs the CSV path — **use this to tell a real Mongo hit from a silent fallback.**
  - `/api/track/layouts` (`get_track_layouts`): light projection for the LAYOUT dropdown; returns 200 + `[]` on Mongo-down (never 500) so the dropdown just hides.
  - `_transform_mongo_doc`: Mongo points carry no `normalizedDistance`/`corner_*` — those are derived; corners built from the doc's `corners[]`. Stride-samples to `TRACK_MAX_POINTS`.
- `telemetry-comparison/mongo_settings.py` — `MONGO_*` env (DB default `test_manager`), `TRACK_LAYOUTS_COLLECTION` (default `track_layouts`), `TRACK_MAX_POINTS` (3000). Read-only client configured lazily in `main.py` lifespan.
- Frontend: `static/app.js` (`getActiveTrack`, `refreshTrackForActive`), `static/modules/data.js` (`fetchTrack`, `fetchLayouts` — monotonic load-token guard against stale geometry), `static/modules/selections.js` (the lake-sourced cascading dropdowns incl. `track`).

## track_layouts doc shape

```
_id: "<track>/<layout>"     e.g. "spa/spa", "ks_highlands/long"
track: "<ac_folder_name>"   LOWERCASE AC folder name (spa, ks_nurburgring, monza…)
layout: "<config>"          spa, long, short, int, drift…
length_m, n_points, n_corners
points: [{x, z, distance_m, radius_m, speed_kmh, gradient_pct, width_total_m}, …]
corners: [{id, type, direction, distance_start_m, distance_end_m, min_radius_m}, …]
```

## The casing gotcha (root cause of "map shows Nürburgring")

- Mongo keys are **lowercase AC folder names** (`spa`, `_id: "spa/spa"`).
- The lake `ac_telemetry.track` value is **capitalized** (`Spa`).
- A case-SENSITIVE `find({track: "Spa"})` misses `spa` → `_resolve_mongo_doc`
  returns `None` → `/api/track` CSV-falls-back to
  `DEFAULT_TRACK_CSV = tracks/ks_nurburgring/layout_sprint_a.csv` → the map shows
  Nürburgring no matter what you selected. The fix is a **case-insensitive
  anchored match** on `track` / `_id` (regex `^<re.escape(value)>$`, `$options:"i"`).
- Always verify with the `track_file` field: `mongo:spa/spa` = real Mongo hit;
  a `.csv` path = silent fallback.

## Inspecting the collection without Mongo access

The `track-viewer` service is a read-only UI over the same `track_layouts`
collection. Its `GET /api/tracks` lists every layout (`_id`, `track`, `layout`,
`length_m`, `n_corners`). Public URL pattern:
`https://track-viewer-<workspace>.deployments-dev.quix.io/api/tracks`. Use it to
confirm how a track is keyed before debugging a lookup miss.

## Related

- `video-seeking` skill — marker↔video sync in the same `static/` frontend.
- The track value feeding all this comes from the LAKE session dropdown
  (`/api/sessions`), not from Mongo — so a track only appears in the dropdown if
  the lake has telemetry for it, even though its geometry lives in Mongo.
