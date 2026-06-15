# Architecture: Track-Layouts MongoDB Import

One-shot in-cloud job (`track-importer/`) that bulk-imports the extracted AC
track-layout geometry (CSV + corner analysis JSON) into a new MongoDB
collection `track_layouts`, one document per layout, so the bridge can serve
track geometry and telemetry can join to a layout by `track` +
`trackConfiguration`. Additive only: no existing service write path is touched.

## Why this architecture

- **One doc per layout with an embedded `points` array** (not one doc per
  point). The natural read unit is "fetch a layout"; a layout's full geometry
  stays well under Mongo's 16 MB doc limit (largest observed: trento-bondone,
  11,082 points ≈ 2.78 MB BSON), so embedding avoids a per-read aggregation and
  ~100k+ point documents.
- **Bundled data inside the image.** The CSVs are copied under
  `track-importer/data/tracks_csv/` and pulled into the image by the
  dockerfile's `COPY "${MAINAPPPATH}" /app`. The job has no runtime dependency
  on the LapTimeEstimator repo or object storage — it runs offline against the
  in-cluster `mongodb` host using injected `MONGO_*` secrets.
- **Copied `MongoSettings` / connection conventions, not cross-imported.**
  `settings.py` and `mongo.py` replicate `test-manager-backend/api/{settings,mongo}.py`
  (same `env_prefix="MONGO_"`, same fields/defaults, identical `MongoClient`
  kwargs). The two apps have separate Docker build contexts; a small copied
  class is cleaner than build-context gymnastics.
- **Idempotent upsert** (`replace_one({"_id": id}, doc, upsert=True)`) keyed on
  `_id = "<track>/<layout>"`. Re-running overwrites cleanly with no duplicates.
- **Heuristic `trackConfiguration` with an override hook.** The importer cannot
  know AC's exact config string for every layout, so it applies a best-effort
  rule and prints every derived pair for the user to eyeball, with a
  `config_map.json` override consulted first.

## Data flow

```
data/tracks_csv/<track>/layout_<layout>.csv          (14-col geometry)
data/tracks_csv/<track>/layout_<layout>_corners.json (corner analysis)
        |
        | importer.discover_layouts(data_dir)
        |   - glob */layout_*_corners.json  (corners file = in-scope gate)
        |   - require matching layout_<layout>.csv (error if missing)
        |   - group by track, count layouts per track
        v
   {track: [(layout, csv_path, corners_path), ...]}
        |
        | per layout:
        |   derive_track_configuration(track, layout, layout_count, config_map)
        |     1. config_map["<track>/<layout>"] override if present
        |     2. else multi-layout track -> trackConfiguration = layout
        |        else single-layout track -> trackConfiguration = "track config"
        |   build_document(...)
        |     - _parse_points: typed numbers; blank speed_* -> null
        |     - length_m: corners.json total_length_m, else max(distance_m)
        |     - embed points[], corners[], corners_meta{config,generated_at,version}
        |   doc_bson_size(doc): assert < 16 MB, warn near 8 MB
        v
   --dry-run : print summary row, no writes
   live      : collection.replace_one({_id}, doc, upsert=True)
        v
   Final summary table + TOTALS + heuristic warning + active-DB note
```

## trackConfiguration derivation (AC join semantics)

AC identifies a track by static fields `track` (folder id) and
`trackConfiguration` (layout name; **the literal string `"track config"` for
single-layout tracks**, which is what AC's shared memory reports for tracks
without layouts). `derive_track_configuration` (importer.py) is the single
override-able function:

1. `config_map.json` exact override `{"<track>/<layout>": "<ac_config>"}`.
2. Multi-layout track (>1 in-scope layout) → `trackConfiguration = layout`
   (e.g. `ks_nurburgring/gp_a` → `gp_a`).
3. Single-layout track → `trackConfiguration = "track config"` (e.g.
   `spa/spa` → `"track config"`), while the raw token is preserved in the
   `layout` field.

Single-vs-multi is detected by counting in-scope layout CSVs per track in the
bundled data. The job prints a warning whenever any value came from the
heuristic so the user verifies the pairs against AC's real config strings.

## Document schema (`track_layouts`)

| field                | type            | source |
|----------------------|-----------------|--------|
| `_id`                | string          | `"<track>/<layout>"` |
| `track`              | string          | folder id (AC `track` static field) |
| `trackConfiguration` | string          | derived (see above) |
| `layout`             | string          | raw `<layout>` token from filename |
| `length_m`           | float           | corners.json `total_length_m`, else max `distance_m` |
| `n_points`           | int             | point count |
| `n_corners`          | int             | corners array length |
| `source`             | string          | `"LapTimeEstimator fast_lane v7"` |
| `imported_at`        | datetime (UTC)  | job run time, tz-aware |
| `points[]`           | array of object | 14 numeric fields; blank `speed_ms`/`speed_kmh` → `null` |
| `corners[]`          | array of object | corners.json `corners` verbatim |
| `corners_meta`       | object          | `{config, generated_at, version}` from corners.json |

Note: `corners_meta.config` is the corner-detection thresholds from the source
JSON, not the AC config string.

Index (created idempotently in `mongo.connect`):
`track_layouts.create_index([("track", 1), ("trackConfiguration", 1)])` — the
telemetry join key. `_id` is already unique.

## File inventory

Created under `track-importer/`:

- `settings.py` — `MongoSettings` (copied from backend; `env_prefix="MONGO_"`,
  host default `mongodb`, db default `test_manager`, computed `url`).
- `mongo.py` — `connect(settings)` (identical `MongoClient` kwargs to the
  backend) + `track_layouts` compound index; `disconnect`, `get_mongo`.
- `importer.py` — discovery, point parsing, `trackConfiguration` derivation,
  doc builder, BSON-size guard. Uses stdlib `csv`/`json` + pymongo's bundled
  `bson` (no pandas).
- `main.py` — CLI (`--dry-run`, `--data-dir`, `--database`, `--config-map`),
  upsert loop, summary table, TOTALS, warnings. Entrypoint matches `app.yaml`.
- `config_map.json` — empty `{}` override template.
- `pyproject.toml` + `uv.lock` — uv-based deps (pymongo, pydantic,
  pydantic-settings), `requires-python >=3.13`.
- `dockerfile` — `python:3.13-slim-bookworm` + uv, `COPY` app (incl. `data/`),
  `uv sync --frozen`, `ENTRYPOINT ["/bin/uv", "run", "-m", "main"]`.
- `app.yaml` — declares `MONGO_USER`/`MONGO_PASSWORD` (Secret),
  `MONGO_HOST` (default `mongodb`), `MONGO_PORT` (27017),
  `MONGO_DATABASE` (default `test_manager`, overridable).
- `data/tracks_csv/<track>/layout_<layout>.csv` + `_corners.json` — 29 layout
  pairs across 15 tracks, copied from `LapTimeEstimator/tracks_csv/`.

## Integration points

- **Mongo cluster** — connects to the in-cluster `mongodb` service (quix.yaml)
  using the deployment's `MONGO_*` secrets, the same path the backend uses.
- **DB name** — locked to `test_manager` (the DB the backend connects to;
  `test-manager-backend/api/settings.py` line 21). The quix.yaml DCM config
  references `ac_telemetry` in places, so the DB is kept overridable via
  `MONGO_DATABASE` / `--database` without code changes.
- **Telemetry join (downstream, not built here)** — `(track, trackConfiguration)`
  matches AC's static fields, letting telemetry rows join to a layout. The
  compound index supports that lookup.
- **No write-path changes** to `test-manager-backend` or `leaderboard-service`;
  this app only creates and writes the new `track_layouts` collection.

## Caveats

- `trackConfiguration` heuristic values are unverified against AC's real config
  strings — the biggest correctness risk; telemetry joins fail silently if
  wrong. Verify the printed pairs, supply `config_map.json` as needed.
- The source CSVs are fast_lane AI ideal-line extractions, not track
  centerlines — confirm that is the intended geometry for the consumer.
