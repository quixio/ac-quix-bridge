# Auto Session-End Trigger — Design

**Shortcut ticket:** TBD
**Branch:** TBD (off `feature/sc-72747/build-post-race-ai-analyzer-pipeline` or `main` after merge)
**Date:** 2026-06-01
**Status:** Spec — awaiting plan + implementation
**Supersedes:** "v2 auto-trigger" bullet in `2026-05-21-post-race-ai-summary-design.md` §1

## 1. Goal + scope

Fire the existing Post-Race AI Summary runner **automatically** when an AC racing session ends, without requiring the user to click Analyze in the TM frontend. "Session end" = `ac-telemetry-raw` goes silent for a given `session_id` for longer than a configured threshold (default 90 s).

### In scope

- New micro-service `ac-postrace-trigger` (Python + QuixStreams `Application`) deployed via `quix.yaml`
- Consumes `ac-telemetry-raw`, extracts `session_id` from payload, drives a per-key silence detector
- On timeout: POST `/api/v1/analyses` against `test-manager-backend` with discovered `test_id` + `session_id`
- Test_id resolution via DCM lookup keyed on `hostname` (same path as `session-config-bridge`)
- Reuse `StreamTimeoutTracker` from QuixStreams branch `feature/sc-72221/adding-timeout-based-trigger-in-quixlake-sink` (stdlib-only — vendor the single file until that branch merges to main)
- Idempotence: one analysis per `(test_id, session_id)`; tracker fire-and-evict semantics guarantee one fire per silence period per key
- Pytest unit tests (mock tracker callback, mock TM POST)
- Quix Cloud deployment config

### Deferred / out of scope

- Replacing manual Analyze button — both paths coexist (manual still works for re-analysis)
- Replace-on-reanalyze logic (already a pending open issue in `project_post_race_ai_summary`)
- Per-test rollup re-trigger
- Notifying user from frontend when an auto-analysis lands (frontend already polls `/analyses`)
- AC-side end signals (`live → off/pause` transitions in `ac-telemetry-source`) — silence detection alone covers the case; AC transitions are a nice-to-have for lower latency, can come later
- Pod-restart durability — tracker state is in-memory; sessions whose silence period spans a restart will not fire automatically. Manual Analyze remains the safety net.

## 2. Architecture

```
ac-telemetry-source → ac-telemetry-raw
                            │
                            ▼ consume
                  ┌─────────────────────────┐
                  │ ac-postrace-trigger     │
                  │                         │
                  │  per message:           │
                  │   session_id ←          │
                  │     value["session_id"] │
                  │   tracker.touch(sid)    │
                  │                         │
                  │  on silence (90 s):     │
                  │   resolve test_id ←     │
                  │     DCM lookup(host)    │
                  │   POST /api/v1/analyses │
                  └────────────┬────────────┘
                               │ HTTPS + Bearer
                               ▼
                  ┌─────────────────────────┐
                  │ test-manager-backend    │
                  │  /api/v1/analyses POST  │
                  │  → spawn runner task    │
                  │  → BatchAnalysisAI.run  │
                  └─────────────────────────┘
```

## 3. Component: `ac-postrace-trigger`

### Files (proposed)

```
ac-postrace-trigger/
├── app.yaml
├── dockerfile
├── main.py                      # QuixStreams Application + tracker wiring
├── trigger.py                   # callback: DCM lookup + TM POST
├── stream_timeout_tracker.py    # vendored from quix-streams sc-72221 (stdlib-only)
├── pyproject.toml
├── tests/
│   ├── test_trigger.py          # callback unit tests
│   └── test_main.py             # end-to-end touch → fire mock
└── README.md
```

### `main.py` shape

```python
import os
from quixstreams import Application
from stream_timeout_tracker import StreamTimeoutTracker
from trigger import on_session_silent

THRESHOLD_MS = int(os.environ.get("SESSION_TIMEOUT_MS", "90000"))

app = Application(consumer_group="ac-postrace-trigger", auto_offset_reset="latest")
raw_topic = app.topic(os.environ["INPUT"], key_serializer="bytes", value_deserializer="json")

tracker = StreamTimeoutTracker(
    stream_timeout_ms=THRESHOLD_MS,
    on_stream_timeout=on_session_silent,
)

sdf = app.dataframe(raw_topic)
sdf = sdf.update(lambda value: tracker.touch(value["session_id"]))

if __name__ == "__main__":
    tracker.start()
    try:
        app.run(sdf)
    finally:
        tracker.stop()
```

Latest offsets only (no replay through historical silence). One consumer group → one tracker dict in memory.

### `trigger.py` callback

```python
def on_session_silent(session_id: str) -> None:
    """Tracker callback (sync, must not block the consumer thread)."""
    try:
        test_id = resolve_test_id_via_dcm(hostname=HOSTNAME)
    except DcmLookupError:
        log.warning("session_id=%s silent but no test_id in DCM", session_id)
        return

    requests.post(
        f"{TM_BACKEND_URL}/api/v1/analyses",
        headers={"Authorization": f"Bearer {QUIX_TOKEN}"},
        json={"test_id": test_id, "session_id": session_id},
        timeout=2.0,           # callback budget tight; fire-and-forget
    )
```

DCM lookup mirrors `session-config-bridge` — same DCM endpoint, same `target_key=hostname` shape.

### Threshold tuning

- 90 s default. Long enough that AC pause for "let me grab coffee" doesn't trigger; short enough that user gets summary within ~2 min of finishing.
- Configurable via `SESSION_TIMEOUT_MS` env var per workspace.
- Background daemon polls every `max(100, min(1000, THRESHOLD_MS // 5))` ms — i.e. ~18 s for 90 s threshold.

## 4. Vendor `stream_timeout_tracker.py`

Copy `quixstreams/sinks/core/stream_timeout_tracker.py` from `feature/sc-72221/adding-timeout-based-trigger-in-quixlake-sink` verbatim into our service. Add file header:

```
# Vendored from QuixStreams sc-72221 branch (commit <sha>).
# Replace this file with `from quixstreams.sinks.core.stream_timeout_tracker import StreamTimeoutTracker`
# once that branch merges to main and we bump our quixstreams pin.
```

Stdlib-only → no extra deps. Tests for the tracker live in the upstream branch; we don't duplicate them, just add integration tests for `trigger.py` callback wiring.

## 5. Contracts

### Input — `ac-telemetry-raw`
- Producer: `ac-telemetry-source` (`ac_source.py:142`)
- Kafka key: `hostname` (bytes)
- Value: JSON. Required fields for this service: `session_id` (string, ISO+Z timestamp), `timestamp_ms` (int). All other AC telemetry fields ignored.
- Cadence: ~60 Hz while AC `status == "live"`. Silent when paused / quit / replay.

### Output — `POST /api/v1/analyses`
Body:
```json
{
  "test_id": "TST-XXXX",
  "session_id": "2026-05-29T09:39:06.113Z"
}
```
Returns `201 Created` with `analysis_id`. Service logs the response but does not persist it; the in-flight analysis lives in TM Mongo.

Auth: `Authorization: Bearer ${QUIX_TOKEN}` — same workspace SDK token Test Manager already trusts.

## 6. Edge cases + gotchas

| Case | Behaviour |
|---|---|
| AC paused for >90 s, then resumed | Tracker fires, evicts, posts analysis. Resumed telemetry creates a fresh tracker entry with the same `session_id`. Second silence will fire a second analysis. **Acceptable** — manual replace-on-reanalyze (separate open issue) will dedupe. |
| AC quit cleanly | `ac_source.py:129` guard stops producing → topic silent → fires once. ✓ |
| Sim PC crash | Same as above. ✓ |
| DCM has no active test for hostname | Warning logged, no POST. Analysis can still be triggered manually later. |
| TM backend down at callback time | HTTPException logged, no retry. Lost trigger; user can manually run. (Retry logic deferred — keeps callback bounded.) |
| Tracker callback raises | Tracker logs + swallows per its contract; key already evicted so won't fire again for current silence period. Resumes normally on next `touch`. |
| Two consumer instances (replicas=2) | Each maintains its own in-memory tracker dict. Kafka partition assignment keeps each session on one consumer (key = hostname). Single-replica recommended initially. |
| Service restart mid-silence | Tracker dict lost. Dormant sessions don't fire post-restart. Manual Analyze remains. |
| Callback blocks > consumer heartbeat | Heartbeat timeout → rebalance cascade. Mitigated by 2 s POST timeout + no synchronous flushes. |
| Multiple AC instances same hostname | DCM lookup returns one test_id, all sessions attributed to it. Future enhancement: lookup keyed on `(hostname, session_id)`. |

## 7. Test plan

### Unit (pytest, in `ac-postrace-trigger/tests/`)

- `test_trigger.py::test_posts_to_tm_on_callback` — mock DCM, mock `requests.post`, assert called with expected body.
- `test_trigger.py::test_skips_when_dcm_has_no_match` — DCM raises → POST not called.
- `test_trigger.py::test_swallows_http_error` — `requests.post` raises → no propagation.
- `test_main.py::test_touch_and_fire` — feed N messages to tracker, sleep > threshold, assert callback invoked exactly once per session_id.

### Integration (manual against dev workspace)

1. Deploy `ac-postrace-trigger` to dev workspace.
2. Start AC, drive 2 clean laps + 1 partial, quit to menu.
3. Wait 95 s.
4. Verify TM Mongo `analyses` collection has a doc with `triggered_by="auto"` (new field — see §8) and the expected `(test_id, session_id)`.
5. Verify TM frontend AI Summary tab shows the analysis once it lands.

### Regression

- Manual Analyze button still works (no auto run blocks it).
- `session-config-bridge` session-start path unaffected (different consumer group).

## 8. Backend tweak (optional but recommended)

Add `triggered_by: Literal["manual", "auto"]` to `AnalysisCreate` model and persist on the doc. Defaults to `"manual"`. The auto-trigger service sets `"auto"`. Frontend can use this to differentiate origin in the history dropdown.

Single field; doesn't change any existing behaviour. Skippable for MVP.

## 9. Deployment (`quix.yaml`)

```yaml
- name: ac-postrace-trigger
  application: ac-postrace-trigger
  version: latest
  deploymentType: Service
  resources:
    cpu: 100
    memory: 256
    replicas: 1            # single replica — in-memory state, no coordination
  desiredStatus: Running
  variables:
    - name: INPUT
      inputType: InputTopic
      required: true
      value: ac-telemetry-raw
    - name: SESSION_TIMEOUT_MS
      inputType: FreeText
      defaultValue: "90000"
    - name: TM_BACKEND_URL
      inputType: FreeText
      required: true
    - name: DCM_URL
      inputType: FreeText
      required: true
    - name: QUIX_TOKEN
      inputType: Secret
      secretKey: QUIX_TOKEN
      required: true
```

## 10. Migration / rollout

1. Implement + test locally (mocked DCM + mocked TM).
2. Deploy to dev workspace with `replicas: 0` to validate config without firing.
3. Bump to `replicas: 1`, drive a test session, watch the loop.
4. Run for a week with manual Analyze as parallel sanity check; compare auto vs manual runs.
5. If stable, deferred items can be picked up (replace-on-reanalyze, `triggered_by` field, AC-side end signal for sub-90s latency).

## 11. Related

- `project_post_race_ai_summary` memory — full feature state, `shared/post_race_ai/` runner
- `project_quixlake_mcp` memory — lake-side MCP tools
- `reference_lake_session_id_timestamp_trap` — session_id ISO+Z handling (our POST body uses the raw value from `ac-telemetry-raw`, already ISO+Z, so the trap doesn't fire here)
- `feature/sc-72221/adding-timeout-based-trigger-in-quixlake-sink` (quix-streams) — source of vendored tracker; track for merge to main
