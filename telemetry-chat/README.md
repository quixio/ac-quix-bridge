# telemetry_chat

Natural-language chat → structured query → QuixLake plot.

A user types "plot all ludvik's laps in BMW"; Quix AI (chat agent, no workspace)
returns a list of `{session_id, lap, signal}` tuples; the backend fans those
out to QuixLake via the same query pipeline `telemetry-comparison` uses; the
frontend overlays the responses on one Plotly chart per signal.

## Architecture

```
browser ─── POST /api/plot {message, session_id?} ───► backend
                                                         │
                                                         ├─ first turn: POST /ai/api/sessions {}  (new Quix AI session)
                                                         │
                                                         └─ POST /ai/api/sessions/{sid}/messages
                                                            (first turn: prepend condensed channels + sessions list)
                                                            ◄── SSE stream
                                                            parse structured JSON
                                                            │
                                                            ├─ "plot" → fan out GET /ac_telemetry telemetry lookups
                                                            │          → return {session_id, traces: [{x, y, name, ...}], ...}
                                                            │
                                                            └─ "clarify" → return {session_id, question, options}
```

Sessions cache: backend walks QuixLake `/partitions` once per TTL (default 60s)
and keeps the tree in memory. Channels (from `channels.json`) are loaded at
startup and condensed into a ~3 KB text block for the LLM prompt.

## Local dev

```bash
cp .env.example .env  # fill in QUIX_TOKEN, QUIXLAKE_URL, QUIX_LAKE_TOKEN
uv sync
uv run python main.py
# http://127.0.0.1:8771
```

Frontend dev deps:
```bash
npm install
```

## Tests

```bash
uv run pytest                 # backend
npm test                      # frontend (vitest)
```

Or use the global `/test` skill from this directory to run the whole gate.
