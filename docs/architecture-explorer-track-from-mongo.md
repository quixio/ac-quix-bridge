# Architecture — Telemetry Explorer: track geometry from MongoDB

## What it does

The Telemetry Explorer (`telemetry-comparison/`) now loads track geometry +
corners from MongoDB `track_layouts` (the track-importer's output) based on the
user's **track dropdown** selection, instead of a single hardcoded CSV. A new
app-level **LAYOUT dropdown** appears only when the selected track has more than
one layout (e.g. `ks_nurburgring` → `sprint_a/b`, `gp_a/b`). The bundled CSV
remains as an offline / Mongo-down / pre-selection fallback, and the
`/api/track` response shape — the contract the map/chart/sync consumers depend
on — is byte-for-byte the same regardless of source.

## Why this architecture

- **Server-side transform, frozen frontend contract.** Both the Mongo doc and
  the CSV are transformed server-side into one `/api/track` shape. This keeps
  `track-map.js`, `charts.js`, `sync.js` (which read `window.trackData`)
  untouched — the alternative (frontend consumes raw Mongo docs) would have
  broken the consumer freeze. `track_file` carries provenance: `mongo:<_id>`
  vs the CSV path.
- **Corners built directly from Mongo `corners[]`, not re-grouped.** Mongo
  points carry no `corner_*` fields; re-deriving corners by grouping fabricated
  per-point fields would be a lossy round-trip. The authoritative Mongo
  `corners[]` array builds the `corners[]` response directly; per-point
  `corner_designation/name/type/direction` are stamped by distance-range purely
  for downstream parity with the CSV path.
- **Lazy, read-only Mongo with hard fallback.** `MongoClient` is constructed
  lazily (5000 ms timeouts), so an unreachable host cannot crash startup —
  failures surface at request time and fall through to CSV. No writes ever
  (mirrors `track-viewer/app/mongo.py`).
- **`os.getenv`-based settings, not pydantic-settings.** The only NEW runtime
  dependency this feature adds is `pymongo`. `mongo_settings.MongoSettings`
  reads the same `MONGO_*` env names/defaults/`url` as track-viewer but via a
  frozen dataclass, avoiding a `pydantic-settings` dependency.
- **Corner naming = `"T<id>"`.** Mongo corners have no human name, only `type`.
  Both `label` and `name` use the placeholder `"T<id>"` (resolved open
  question). Active map track = the **first selection row's** track.
- **DB = `test_manager`** (track-importer + test-manager-backend convention),
  overridable via `MONGO_DATABASE`. quix.yaml DCM references `ac_telemetry` in
  places — noted in `mongo_settings.py`; flip the env var if cloud Mongo holds
  `track_layouts` under a different DB.

## Data flow

```
Track dropdown change (first row)
  → app.js: window.onPartChange wrapper detects col=="track"
  → refreshTrackForActive(track)
       → data.js: fetchLayouts(track)  →  GET /api/track/layouts?track=
            → track_loader: find({track}, light projection) → [{layout,_id,length_m,n_corners}]
              (Mongo down → 200 + empty list, never 500)
       → 0/1 layout → hide LAYOUT dropdown ; >1 → show + populate, auto-select first
       → data.js: fetchTrack(track, layout)  →  GET /api/track?track=&layout=
            → track_loader: _resolve_mongo_doc → find_one(_id="<track>/<layout>")
                 (layout absent → find({track}) sorted, deterministic first)
            → _transform_mongo_doc:
                 stride-sample points (cap=TRACK_MAX_POINTS, keep first/last/corner-bounds)
                 per point: normalizedDistance=distance_m/length_m, severity=_classify_radius,
                            corner_* stamped by distance-range
                 corners[] built directly from Mongo corners[] (index=id, label/name="T<id>",
                            severity from min_radius_m, mid_x/z = nearest sampled point to range mid)
                 track_file="mongo:<_id>"
            → (Mongo down / no doc / no track param) → _load_track_csv(DEFAULT_TRACK_CSV)
                 → (CSV missing) → HTTP 500
       → setTrackData(json) + window.renderTrackMap()  [load-token guard drops stale responses]

LAYOUT dropdown change → onLayoutChange → fetchTrack(activeTrack, newLayout)
```

## Endpoint contracts

- `GET /api/track?track=&layout=` (both optional) — returns the existing
  `/api/track` shape: `{track_file, points[], corners[], total_length_m}`.
  `points[]` keys: `x, z, distance_m, normalizedDistance, radius_m, speed_kmh,
  gradient_pct, width_total_m, severity, corner_designation, corner_name,
  corner_type, corner_direction`. `corners[]` keys: `index, label, name, type,
  direction, severity, start_norm, end_norm, start_m, end_m, min_radius_m,
  mid_x, mid_z`. Identical for CSV and Mongo sources (verified by field-diff in
  the smoke test).
- `GET /api/track/layouts?track=` — `{track, layouts:[{layout,_id,length_m,
  n_corners}]}` sorted by `layout`. Always 200; empty list on Mongo-down/no-docs.

## File inventory

Created:
- `telemetry-comparison/mongo.py` — read-only lazy Mongo client
  (`connect/disconnect/get_mongo`), copied from track-viewer.
- `telemetry-comparison/mongo_settings.py` — `MongoSettings` dataclass +
  `TRACK_LAYOUTS_COLLECTION` (`track_layouts`) + `TRACK_MAX_POINTS` (3000).
- `docs/architecture-explorer-track-from-mongo.md` — this doc.

Modified:
- `telemetry-comparison/track_loader.py` — `_transform_mongo_doc`,
  `_stride_sample_points`, corner-range helpers, `_resolve_mongo_doc`;
  `GET /api/track` gains `track`/`layout` params + Mongo→CSV precedence; new
  `GET /api/track/layouts`. `_load_track_csv` and `tracks/` retained unchanged.
- `telemetry-comparison/main.py` — Mongo `connect()`/`disconnect()` in lifespan.
- `telemetry-comparison/requirements.txt`, `pyproject.toml` — add `pymongo`.
- `telemetry-comparison/static/modules/data.js` — `fetchLayouts(track)`;
  `fetchTrack(track, layout)` with monotonic load-token race guard.
- `telemetry-comparison/static/app.js` — active-track resolution,
  `refreshTrackForActive`, `onLayoutChange`, `window.onPartChange` wrapper,
  init re-fetch wiring.
- `telemetry-comparison/static/index.html` — `#layout-select` in Track Map header.
- `quix.yaml` — 5 `MONGO_*` vars on the `telemetry-comparison` deployment.

## Integration with neighbouring features

- Reads the same `track_layouts` collection that **track-importer** writes and
  **track-viewer** reads — same `_id="<track>/<layout>"` convention, same
  read-only client kwargs, same `MONGO_*` secret wiring (`MONGO_USER`→`MONGO_USER`,
  `MONGO_PASSWORD`→`MONGO_ROOT_PASSWORD` secrets).
- The `track` value driving the lookup is the explorer's existing partition-column
  dropdown (`state.js:PART_COLS`); no new selection model.
- Consumers `track-map.js` / `charts.js` / `sync.js` and `state.js` setters are
  unchanged.
