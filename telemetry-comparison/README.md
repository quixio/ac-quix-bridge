# Telemetry Comparison

FastAPI service that serves a Plotly UI for cross-run/lap telemetry comparison, backed by QuixLake queries. Embeds in the Test Manager Analysis tab as an iframe.

## Files

- **`main.py`** — FastAPI app + plotting routes (`/api/sessions`, `/api/telemetry`, `/api/channels`).
- **`config.py`** — Central config: environment variables, paths, rendering constants.
- **`auth.py`** — Bearer-token ASGI middleware gating `/api/*` routes. Validates `Authorization: Bearer <token>` against Quix Portal via the `quixportal` SDK. Public paths (`/`, `/static/*`, `/health`) bypass so the SPA can boot.
- **`local_auth.py`** — Dev mock implementing the same interface; activated by `LOCAL_DEV_MODE=true`.
- **`chat.py`** — `POST /api/chat` JSONL streaming route forwarding the Quix AI QuixLake Querier agent.
- **`plans.py`** — Pydantic models for the agent's structured plot/clarify output.
- **`quix_ai.py`** — httpx client for Quix AI sessions + SSE message streaming.
- **`track_loader.py` / `video_proxy.py` / `partition_walker.py`** — track config, MP4 proxy, lake partition walking.
- **`static/`** — Frontend: HTML, ES-module JavaScript (chart rendering, video sync, AI chat), CSS.
- **`channels.json`** — Telemetry field metadata (names, units, axis ranges).

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `Quix__Workspace__Id` | (auto-injected) | Workspace ID used when validating user Bearer tokens. Set by Quix Cloud; for local dev populate via `.env`. |
| `API_AUTH_ACTIVE` | `true` | Set to `false` to bypass auth entirely (tests, some local flows). |
| `LOCAL_DEV_MODE` | `false` | When `true`, swaps in `LocalAuth` (all permissions granted) instead of calling Quix Portal. |
| `Quix__Lakehouse__Query__Url` | (auto-injected on byox) | QuixLake base URL. Falls back to legacy `QUIXLAKE_URL` for local dev. |
| `Quix__Lakehouse__Query__AuthToken` | (auto-injected on byox) | Bearer token for QuixLake API. Falls back to legacy `QUIX_LAKE_TOKEN` for local dev. |
| `Quix__Lakehouse__Catalog__Url` | (auto-injected on byox) | Iceberg catalog base URL (used by `/api/sessions` via manifest endpoint). Falls back to legacy `CATALOG_URL`. |
| `Quix__Lakehouse__Catalog__AuthToken` | (auto-injected on byox) | Bearer token for catalog API. Falls back to legacy `CATALOG_TOKEN`. |
| `TABLE_NAME` | `ac_telemetry` | QuixLake table name. |
| `BLOB_VIDEO_PREFIX` | `ac_video` | Blob storage prefix for MP4 recordings + sidecar JSONs. |

## AI chat

The chat panel calls Quix AI's QuixLake Querier agent via `POST /api/chat`. Required env vars:

| Var | Default | Notes |
|---|---|---|
| `Quix__Portal__Api` | (required) | Quix Portal API base. Auto-injected in Quix Cloud. |
| `QUIX_TOKEN` | (optional) | Fallback PAT for the AI chat; used only in local dev without auth. Deployed, the chat forwards the logged-in user's Bearer. |
| `AC_TELEMETRY_AGENT_ID` | `d578e2f5-c2b7-461a-90d2-70dfac450fb0` | AC Telemetry Agent config ID. Per-environment — set as a project variable (byox/dev differ). |

The agent's system prompt + knowledge bases live on the agent itself; this service only forwards user messages and streams responses.
