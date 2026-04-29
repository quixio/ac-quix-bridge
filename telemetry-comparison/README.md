# Telemetry Comparison

FastAPI service that serves a Plotly UI for cross-run/lap telemetry comparison, backed by QuixLake queries. Embeds in the Test Manager Analysis tab as an iframe.

## Files

- **`main.py`** — FastAPI app + plotting routes (`/api/sessions`, `/api/telemetry`, `/api/channels`).
- **`config.py`** — Central config: environment variables, paths, rendering constants.
- **`auth.py`** — Shared-password HTTP Basic ASGI middleware gating every route + the static mount. Empty `SHARED_PASSWORD` = closed.
- **`chat.py`** — `POST /api/chat` JSONL streaming route forwarding the Quix AI QuixLake Querier agent.
- **`plans.py`** — Pydantic models for the agent's structured plot/clarify output.
- **`quix_ai.py`** — httpx client for Quix AI sessions + SSE message streaming.
- **`track_loader.py` / `video_proxy.py` / `partition_walker.py`** — track config, MP4 proxy, lake partition walking.
- **`static/`** — Frontend: HTML, ES-module JavaScript (chart rendering, video sync, AI chat), CSS.
- **`channels.json`** — Telemetry field metadata (names, units, axis ranges).

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `SHARED_PASSWORD` | (required) | Shared password for HTTP Basic auth on every route. Empty = closed (every request 401). Set as a Quix Secret in cloud deployments; share via password manager. |
| `QUIXLAKE_URL` | (required) | QuixLake base URL. |
| `QUIX_LAKE_TOKEN` | (required) | Bearer token for QuixLake API. |
| `TABLE_NAME` | `ac_telemetry` | QuixLake table name. |
| `BLOB_VIDEO_PREFIX` | `ac_video` | Blob storage prefix for MP4 recordings + sidecar JSONs. |

## AI chat

The chat panel calls Quix AI's QuixLake Querier agent via `POST /api/chat`. Required env vars:

| Var | Default | Notes |
|---|---|---|
| `Quix__Portal__Api` | (required) | Quix Portal API base. Auto-injected in Quix Cloud. |
| `QUIX_TOKEN` | (required) | Bearer token for `/ai/api/...`. SDK token with org access. |
| `QUIX_AI_AGENT_ID` | `d578e2f5-c2b7-461a-90d2-70dfac450fb0` | QuixLake Querier agent UUID. |

The agent's system prompt + knowledge bases live on the agent itself; this service only forwards user messages and streams responses.
