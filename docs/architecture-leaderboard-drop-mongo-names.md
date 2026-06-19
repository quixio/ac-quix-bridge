# Architecture: Leaderboard — drop direct MongoDB, resolve driver names via DCM/lake

**Feature branch:** `leadboard-sp-box`
**Spec:** `dev-planning/leaderboard-drop-mongo-names-via-dcm/spec.md`
**Service:** `leaderboard-service` only

## What the code does

`leaderboard-service` no longer opens any MongoDB connection. It used to
connect to `mongodb:27017` purely to prettify driver display names
(`drivers.name` → display case). That host does not resolve in the dev
workspace, so every name refresh threw
`ServerSelectionTimeoutError: mongodb:27017`, and it violated the standing
directive "Mongo must be reached via DCM, not directly." Driver names are now
sourced from data already in hand and Title-Cased for display:

- **Live (left) row:** the raw DCM-resolved driver carried on each raw tick
  (`_record_message`'s `driver`, originating from `_experiment_cache.driver` /
  the enrichment path — the same source `telemetry-dashboard/main.py` reads off
  `ac-telemetry-config`), Title-Cased (`driver.title()`). This **preserves
  diacritics** (`Ludvík` stays `Ludvík`).
- **Historical (right) table + best-laps:** the lake's `driver` field, which
  was DCM-enriched at lake-write time. It is stored diacritic-folded +
  lowercase, so it is Title-Cased per word (`tomas neubauer` → `Tomas
  Neubauer`). Diacritics are not recoverable here (they were folded out of the
  lake key), which is acceptable.

## Why this architecture

- **No HTTP roster fetch.** DCM has no `/drivers` endpoint — names live inside
  config content, which the live path already consumes. The historical names
  already ride the lake rows. So the Mongo lookup was redundant: both display
  sources exist without any extra network hop. Removing the lookup is strictly
  less plumbing, not a feature trade.
- **Drop the parameter, don't thread empty dicts.** The `mongo`/`lookup`
  parameters were removed from the four routes, `build_live_positions()`,
  `_build_group_rows`, `_solo_active_group`, `_resolve_display_name`,
  `gate_math.to_display_name`, and the app lifecycle — rather than threading
  `{}` through. This deletes `api/mongo.py`, the `MongoSettings`, the
  `Depends(get_mongo)` injection, and the `pymongo` dependency entirely.
- **Raw DCM name on the live row (resolved spec §8 open question).** Before
  this change the live row used `_resolve_display_name(_fold_for_lookup(driver),
  lookup)` — i.e. the **folded** key when the Mongo lookup missed. The raw DCM
  `driver` is in scope at that point, so we switched to `driver.title()` to keep
  diacritics. The HTTP snapshot active-row builders (`_build_group_rows` merged
  row, `_solo_active_group`) were changed to the **same** `driver.title()` of
  the raw active driver — NOT the folded key — so the HTTP snapshot `driver`
  string is byte-identical to the WS `{"type":"active"}` envelope. The frontend
  matches the live row to its snapshot row by exact string equality; a mismatch
  would freeze the live timer. The historical (right) rows still title-case the
  folded lake key (no raw name in hand there); for ASCII names this matches.

## Data flow (driver name, after the change)

```
LIVE (left) row:
  ac-telemetry-config ──► _experiment_cache.driver ──► raw tick `driver`
    └─► _record_message ──► display = driver.title()  ──► WS {"type":"active"}.driver
                                                       └─► HTTP snapshot active row.driver
                                                           (build_live_positions →
                                                            _build_group_rows / _solo_active_group)

HISTORICAL (right) table + /best-laps:
  lake `driver` (DCM-enriched at write, folded+lowercase)
    └─► _fold_driver_name (match key)
    └─► display = folded.title()   (leaderboard_dropdowns /best-laps,
                                      leaderboard_real _build_group_rows historicals,
                                      live_telemetry _resolve_display_name)
```

No MongoDB node anywhere in the path.

## File inventory

**Deleted**
- `leaderboard-service/api/mongo.py` — entire client module (`connect`,
  `disconnect`, `get_mongo`, `_create_indexes`, `_mongo`).

**Modified**
- `api/settings.py` — removed `MongoSettings`, the `mongo` field, and the
  now-unused `computed_field` / `SettingsConfigDict` imports.
- `api/live_telemetry.py` — removed `_get/_refresh/_invalidate_driver_name_lookup`,
  the module-level lookup state + lock + missing-key set; simplified
  `_resolve_display_name(folded_key)` to `folded_key.title()`; live-row publish
  now uses raw `driver.title()`; removed the `_invalidate_driver_name_lookup()`
  call in the best-laps refresh; removed Mongo from `_broadcast_full_snapshot_safely`.
- `api/routes/leaderboard_real.py` — deleted `_build_driver_name_lookup`;
  `build_live_positions()` no longer takes `mongo`; `_build_group_rows` /
  `_solo_active_group` drop the `driver_name_lookup` param and use raw
  `driver.title()` for the active row. `_fold_driver_name` kept (match key).
- `api/routes/leaderboard.py`, `leaderboard_stream.py`, `leaderboard_dropdowns.py`
  — removed `pymongo`/`get_mongo` imports and the `Depends(get_mongo)` params;
  `/best-laps` display is `folded.title()`.
- `api/gate_math.py` — `to_display_name(folded_key)` simplified to title-case
  (parameter dropped; function currently has no callers but kept per spec).
- `api/app.py` — removed `mongo` import + `mongo.connect/disconnect`; LOCAL_DEV
  log no longer mentions MongoDB.
- `pyproject.toml` + `uv.lock` — `pymongo` (and transitive `dnspython`) removed.
- `app.yaml` + `quix.yaml` (leaderboard-service block only) — removed
  `MONGO_USER`, `MONGO_PASSWORD`, `MONGO_HOST`.

## Integration notes

- The live WS envelope and HTTP snapshot active-row `driver` strings MUST stay
  identical — both now derive from `raw_driver.title()`. Any future change to
  one must change the other (frontend exact-equality match).
- Several neighbouring architecture docs
  (`architecture-leaderboard-live-stream.md`, `-live-positions.md`,
  `-dual-mode.md`, `-checkpoint-gates.md`) still describe the old
  `build_live_positions(mongo)` signature and the Mongo display-case lookup.
  Those are now stale; this doc supersedes their Mongo-related claims. DocuGuy
  should treat this file as the source of truth for driver-name resolution.
