# F3 — Auto-trigger post-race analysis on session end

Status: SPEC (ready to implement). Branch: `feature/test-manager`.
Supersedes `2026-06-01-auto-session-end-trigger-design.md` (that doc wrongly
assumed the trigger consumes `ac-telemetry-raw` + a StreamTimeoutTracker keyed
by hostname — both wrong, see Verified facts).

## Goal
When an AC session ends, automatically run the post-race AI analysis for that
session — no manual "Analyze" click. F2 (PDF) ✅ + F6 (token forwarding) ✅ are
in; this is the keystone before F4 (auto-email).

## Verified facts (checked in code + live topic data 2026-06-14/15)
- The `test-completed` topic is **already produced** by `ac-telemetry-lake/main.py:73`
  (`_on_stream_timeout`) when a stream goes silent for `STREAM_TIMEOUT_MS` (10s).
  Topic declared `ac-telemetry-lake/main.py:100`, `quix.yaml:712`. **No producer
  change needed.**
- Event shape (verified against the live topic):
  ```json
  {"event":"test-completed","key":"<session_id>","stream_timeout_ms":10000,"timestamp":"<iso>"}
  ```
- **`key` is the `session_id`** (a UTC-timestamp string, e.g. `2026-06-05T16:32:20.885Z`),
  NOT a hostname. Mechanism: `ac-telemetry-lake/main.py:173` `sdf.group_by("session_id")`
  re-keys the stream before the sink, so the timeout fires per session_id. (The
  source keys raw by hostname `ac_source.py:36,50`, but the lake re-groups.)
- **The same `session_id` RE-FIRES** — the timeout re-emits while a session stays
  silent (observed: many consecutive events with the same key). So the consumer
  MUST be idempotent / dedup per session.
- We get **`session_id` only** — no `test_id`, no hostname. So resolution is the
  reverse of `session-config-bridge` (which goes hostname→test_id via DCM).

## Design
Two parts: a thin new consumer service + a small backend change that does the
session_id→test_id resolve and the dedup (backend owns Mongo).

### Part A — backend (`test-manager-backend`)
`POST /api/v1/analyses` (`api/routes/analyses.py`):
1. Make `AnalysisCreate.test_id` optional (`str | None`); add a model validator
   requiring `test_id` OR `session_id`. `triggered_by` already exists (F6).
2. If `test_id` is None: resolve it from session_id —
   `mongo.tests.find_one({"sessions.session_id": payload.session_id})`. 404 if no
   test owns the session. (If >1 match — shouldn't happen — take first + log.)
   `SessionInfo.session_id` is the field; sessions are an array on Test.
3. **Dedup:** before spawning the runner, if a non-failed analysis already exists
   for that `(test_id, session_id)` (status in complete + IN_PROGRESS_STATUSES),
   return that `analysis_id` with 200 instead of starting a new run. Kills the
   re-fire duplicates. (Manual re-analyze can still force a new one — decide:
   dedup only when `triggered_by=="auto"`, so a human can always re-run.)
4. Add index `mongo.tests.create_index("sessions.session_id")` in `api/mongo.py`
   `connect()`.
- Tests: session-only create resolves test_id; 404 when no test owns session;
  auto dedup returns existing; manual still creates fresh; require-one-of validation.

### Part B — new service `ac-postrace-trigger` (own top-level dir)
Files: `main.py`, `app.yaml`, `dockerfile`, `requirements.txt`, + `quix.yaml`
deployment entry. Model on `session-config-bridge` (stateless QuixStreams
consumer, commit every 5s).
- Consume the `test-completed` topic.
- Per event: `POST {TM_BACKEND_URL}/api/v1/analyses` with body
  `{"session_id": event["key"], "triggered_by": "auto"}`.
- Auth for the POST (passes backend `update_permission`): `Quix__Sdk__Token`
  (in-cluster auto-injected; the bridge already auths its POSTs this way —
  `project_test_manager` memory). NOTE: this is the POST auth only; the AI-run
  token is chosen by the backend per F6 (`triggered_by=auto` → `PAT_TOKEN`).
- Dedup is handled backend-side, so the trigger can naively POST every event.
  Optionally keep a small seen-set to cut noise, but not required.
- `TM_BACKEND_URL` default `http://test-manager-backend` (in-cluster k8s svc).
- Backend 404 (session not yet linked to a test) → log + skip (no crash). The
  bridge links sessions at session start via `ac-telemetry-session`, and the
  timeout fires ~10s after the last raw msg, so the link should already exist;
  treat 404 as a benign race / unlinked session.

## quix.yaml
- New deployment `ac-postrace-trigger` (consumer; small resources).
- Input: the `test-completed` topic. Vars: `TM_BACKEND_URL`, consumer group.
  `Quix__Sdk__Token` auto-injected. No new topic (test-completed exists).

## Open decisions (resolve at impl)
- Dedup scope: auto-only (recommended — humans can re-run) vs always.
- Retry on backend 404 (unlinked session) — recommend NO retry, just log+skip.
- Whether to also store `triggered_by` is already done (F6).

## Out of scope
- F4 (auto-email) — separate, builds on this + F2's `render_analysis_pdf`.
- The agent/KB engine — already live on byox (parallel session; see
  `project_post_race_ai_summary`).

## Verification
- Backend: TDD the resolve + dedup + validation (host isolated venv, the
  established pattern). Then on byox: push branch → redeploy backend + deploy
  the trigger → end a session → watch one analysis fire (and re-fires dedup).
- byox post-race agent live: `1f119bcd-…`; backend deployment id
  `005c5e11-19b1-418e-8f0e-eab78353177b`; read logs via
  `quix cloud deployments logs <id> --no-follow --tail N` (byox context).
