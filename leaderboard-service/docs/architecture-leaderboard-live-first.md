# Architecture: Leaderboard live-first / non-blocking DB load

## What this does

The leaderboard renders the **live WebSocket stream immediately** and never
blocks the first paint on the slow lake/database queries. Historical and
DB-backed data — the Best Laps panel, the sector-comparison ghost reference
(gate vectors), and the cascading dropdowns — load **asynchronously** and
patch in when ready. When the lake enumeration is slow or times out (30 s),
the user still sees the live stream first; the DB data arrives afterward, and
its absence degrades to a "loading / empty" placeholder rather than stalling
the live view.

## Why this architecture

The live active-driver path was already independent of the lake: the Kafka
consumer thread (`api/live_telemetry.py::_record_message`) calls
`live_stream.publish_snapshot()` on every raw tick, streaming the active row
over WS. The problem was the **WebSocket connect handshake**, which built the
initial snapshot and the live-session envelope synchronously against the lake.
On a cold cache that meant the first paint waited on
`partition_index.enumerate_groups()` — a `SELECT … GROUP BY` per environment
with a 30 s timeout that times out (`httpcore.ReadTimeout`) on the byox lake.

The fix keeps the proven live path untouched and removes the lake from the
connect path only. We did **not** rewrite enumeration to the fast catalog
`/manifest` endpoint — that is a separate follow-up. This change is purely the
ordering / non-blocking guarantee.

## Blocking point that was fixed

`api/routes/leaderboard_stream.py::live_stream_endpoint` (the WS connect):

1. **Initial snapshot.** `_build_initial_rows_sync` →
   `leaderboard_real.build_live_positions`. On a cold best-laps cache
   (`_best_laps_cache is None`), `build_live_positions` ran a **synchronous**
   `refresh_best_laps_cache` → `_known_groups()` →
   `partition_index.enumerate_groups()` (the 30 s lake call). This blocked the
   snapshot itself — i.e. the live table's base rows — on the lake. This was
   the dominant stall.
2. **Live-session envelope.** `current_live_session_envelope()` →
   `_resolve_session_experiment()` → `enumerate_groups()` again. Sent after the
   snapshot, so it did not delay the live rows, but it delayed the
   `live_session` frame (which drives the Best Laps panel for a bare,
   nobody-lapping session) by up to 30 s.

The frontend (`ui/components/leaderboard-tab.tsx`) was already correct: the
`LivePositionsTable` renders from the WS `liveRows` independently, and the
dropdown / Best Laps fetches run in their own effects via the already-async
`experiment-tree` / `best-laps` routes (both use `asyncio.to_thread` and serve
cached-or-empty data). No frontend change was required.

## What changed

- **`api/routes/leaderboard_real.py::build_live_positions`** — new keyword
  `allow_cold_refresh: bool = True`. When `False` and the cache is cold, it
  serves an empty historicals set (`{}`) instead of running the synchronous
  lake refresh. The polled HTTP `/live-positions` endpoint keeps the default
  (`True`) so it still self-heals on the first request after boot.
- **`api/routes/leaderboard_stream.py`** — the WS connect now:
  - builds the snapshot with `allow_cold_refresh=False` (live table paints
    immediately from the possibly-empty cache);
  - sends a **lake-free** live-session envelope via
    `current_live_session_envelope_fast()`;
  - schedules the lake-aligned resolution
    (`resolve_and_publish_live_session`) on a background task
    (`asyncio.create_task(asyncio.to_thread(...))`) after the client is
    registered, so the resolved experiment broadcasts in (deduped) once the
    lake answers — without ever stalling connect.
- **`api/live_telemetry.py`** — three new helpers:
  - `_build_live_session_envelope_fast` / `current_live_session_envelope_fast`
    — project the adopted session to the wire envelope using track/car from the
    in-memory session record and experiment/environment from the DCM
    `_experiment_cache` only (no `enumerate_groups`). Experiment is `None` when
    DCM hasn't been consulted yet.
  - `resolve_and_publish_live_session` — runs the (possibly slow)
    lake-aligned resolution off the connect path and rebroadcasts the resolved
    envelope via `_publish_live_session_if_changed` (dedupe = no-op on the wire
    when the fast envelope was already correct).

## Data flow (after the fix)

```
WS connect
  ├─ accept (no token gate)
  ├─ snapshot  ← build_live_positions(allow_cold_refresh=False)
  │              cold cache → {} (NO lake call) → live table paints now
  ├─ active_state envelope        (in-memory, lake-free)
  ├─ live_session envelope (FAST)  (track/car + DCM-cache experiment, lake-free)
  ├─ register(client)
  └─ create_task → to_thread(resolve_and_publish_live_session)
                     └─ enumerate_groups() [may take up to 30 s / time out]
                        └─ broadcast resolved live_session (deduped)

Meanwhile, independently and continuously:
  consumer thread raw tick → _record_message → publish_snapshot (active row)
  consumer thread TTL tick  → _maybe_refresh_on_ttl → refresh_best_laps_cache
                              → on change: publish_full_snapshot (historicals)
```

The active driver streams over WS regardless of cache state. Historicals
hydrate via the consumer thread's TTL refresh, which rebroadcasts a populated
snapshot when the lake answers; the connecting client receives that as a normal
`snapshot` frame (full replace).

## What the UI shows while DB data is pending

- **Live positions table** — paints immediately from the WS stream / empty
  base rows; the active row appears as soon as raw ticks flow.
- **Best Laps panel** — `BestLapsPanel` placeholders: "Pick experiment / track
  / car…", "Loading best laps…", or "No historical laps yet…" until the
  `best-laps` fetch returns. Driven by `effectiveExperiment/Track/Car`, which
  come from the live combo / session combo / dropdowns — none of which block
  the live table.
- **Dropdowns** — `treeLoading` spinner until `experiment-tree` returns; an
  empty/timed-out lake leaves them empty without affecting the live table.

## Integration points

- **Live path (untouched):** `live_telemetry._record_message` →
  `live_stream.publish_snapshot` and the WS `{"type":"active"}` mutation
  contract are unchanged.
- **Best-laps / gate-vectors refresh:** still triggered by consumer warm-up,
  AC session message, DCM config event, and the TTL tick
  (`_maybe_refresh_on_ttl`). The connect path no longer competes with these.
- **HTTP `/live-positions`:** unchanged behavior (`allow_cold_refresh=True`).

## Tests

`tests/test_ws_connect_nonblocking.py`:

- `test_cold_cache_ws_path_serves_empty_without_lake` — `allow_cold_refresh=False`
  on a cold cache returns `[]` and asserts `refresh_best_laps_cache` is never
  called.
- `test_cold_cache_http_path_still_refreshes` — default path keeps the
  synchronous cold-cache refresh (no regression to the polled endpoint).
- `test_fast_live_session_envelope_skips_partition_index` — the fast envelope
  resolves experiment from the DCM cache and never calls `enumerate_groups`.
- `test_fast_live_session_envelope_null_when_no_session` — all-null fast
  envelope when nothing is live, still lake-free.

(The two `build_live_positions` cases `importorskip` on pymongo, which is a
declared dep present in CI; they were also verified locally against a pymongo
stub.) The pre-existing `tests/test_live_session_raw_gate.py` suite stays green.

## Adopt-time raw-feed gate (redeploy phantom fix)

`_adopt_live_session` is reached from three paths, all on the consumer thread:
the Kafka session-message handler (`_handle_session_message`), the DCM
session config-event handler (`_handle_config_event`), and the DCM session
prewarm at startup (`_prewarm_session_cache_from_dcm`). On (re)start the
session + config topics are rewound to `OFFSET_BEGINNING`, so a **retained**
announcement (e.g. a legacy "Lamborghini Huracan" session) replays and gets
re-adopted even though no raw telemetry is flowing.

To stop that phantom from lighting the active-stream button on already-connected
tabs, `_adopt_live_session` now broadcasts a **non-null** `live_session`
envelope only when `_raw_feed_is_live()` (a raw tick within
`raw_liveness_window_s`, written solely by `_record_message`). When raw is
quiet it still records `_live_session` (so the metadata labels the flag the
instant raw resumes via `current_live_session()`) but broadcasts **nothing** —
matching `sweep_stale_live_session`, which keeps `_live_session` and only pushes
the cleared envelope when the wire diverges from the gate.

This puts every non-null `live_session` broadcast behind the same raw gate as
`current_live_session()`:
- `_adopt_live_session` — gated directly (above).
- `current_live_session_envelope_fast` (WS connect) and
  `resolve_and_publish_live_session` (background) build from
  `current_live_session()`, which returns `None` without raw.
- `sweep_stale_live_session` only emits the all-null envelope.

The `active_state` envelope is untouched: it is published only from
`_record_message` (raw ticks) and cleared by `sweep_stale_active_state`; no
session/config path writes it. Session/config handlers may still write
`_session_cache` / `_experiment_cache` (enrichment only). Covered by
`tests/test_live_session_raw_gate.py::test_adopt_without_raw_does_not_broadcast_live_envelope`
(and its `_with_raw_` companion).

## Remaining caveat

`live_telemetry._adopt_live_session` (consumer thread, session-message handler)
still calls the lake-resolving `_build_live_session_envelope` (once the raw gate
above is satisfied). That is **off**
the WS-connect path so it does not delay the live view, but a slow lake will
delay the *resolved-experiment* broadcast for a newly-adopted session. The
fast envelope already carries track/car (and DCM-cached experiment), so the
live table and the live-session indicator are unaffected; only the lake-aligned
experiment for the Best Laps panel of a brand-new bare session waits — and it
patches in when the enumeration answers or its TTL cache warms. Switching
enumeration to the catalog `/manifest` endpoint (the separate follow-up) would
remove this last slow call entirely.
