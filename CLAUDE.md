# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AC Quix Bridge is a telemetry + test management platform built on top of **Assetto Corsa**. A Python source reads AC's Windows shared memory at configurable Hz and publishes to Kafka via QuixStreams; downstream services store the data, visualize it, and manage the experiments that produced it.

## Architecture

Deployments are declared in `quix.yaml`. Each lives in its own top-level subdirectory with `app.yaml`, `dockerfile`, a `requirements.txt` or `pyproject.toml`, and `main.py`. Group by role:

### Telemetry ingest
- **`ac-telemetry-source`** — Custom `QuixStreams.Source` reading AC shared memory (physics/graphics/static structs via `ctypes` + `mmap`). Publishes high-frequency telemetry to `ac-telemetry-raw` and session metadata to `ac-telemetry-session` on session change. Windows-only (shared memory requirement); typically runs on the sim PC, not in Quix Cloud.
- **`ac-telemetry-lake`** — `QuixTSDataLakeSink` writing Kafka messages as Hive-partitioned Parquet to blob storage, with optional Iceberg catalog registration. Enriched with DCM config (environment, test_rig, experiment, driver) via `ac-telemetry-config`.

### Viewing
- **`telemetry-dashboard`** — FastAPI + WebSocket live dashboard. Background thread consumes Kafka and broadcasts to browser clients; serves `static/index.html` (Chart.js).
- **`telemetry-comparison`** (Telemetry Explorer) — Lap comparison UI backed by QuixLake queries. Embedded as an iframe inside the Test Manager Analysis tab. See `.claude/skills/video-seeking/` for the bidirectional marker↔video sync contract.

### Video
- **`ac_video_streaming`** — Per-lap MP4 capture with ffmpeg, sidecar JSON samples for telemetry sync, S3 upload. Disabled by default. See `.claude/skills/video-capture/`.
- **`ac-video-viewer`** — Kafka frame viewer (disabled by default).
- **`ac-video-browser`** — Blob storage browser for recorded MP4s.

### Test Manager
- **`test-manager-backend`** — FastAPI + MongoDB. CRUD for Devices, Drivers, Environments, Tests, Logbook. Push experiment configs to the Dynamic Configuration Manager; receive session auto-links from the bridge. Python 3.12 + `uv`.
- **`test-manager-frontend`** — Next.js 14 / React / TypeScript. Two auth modes: Portal `postMessage` (embedded iframe) and localStorage PAT (standalone). Analysis tab embeds Telemetry Explorer via `NEXT_PUBLIC_TELEMETRY_EXPLORER_URL`.
- **`session-config-bridge`** — Consumes `ac-telemetry-session`; looks up the active experiment config in DCM by `target_key=hostname` and POSTs the session to `/api/v1/tests/{test_id}/sessions`. Stateless, commits every 5s.
- **Dynamic Configuration Manager** (Managed deployment, image-only) — Config store for experiment + session configs, backed by MongoDB `ac_telemetry.experiment_configs`. Emits to `ac-telemetry-config` for lake enrichment.
- **`mongodb`** — Shared MongoDB. Two databases: `test_manager` (backend) and `ac_telemetry` (DCM).

### Notebooks
- **`racetelemetryanalysis`**, **`rawdatavisualization`** — Marimo notebooks with Anthropic API integration (BETA).

### Data flow

```
AC shared memory ──► ac-telemetry-source ──► ac-telemetry-raw ──┬──► ac-telemetry-lake ──► blob/Iceberg
                                                                 ├──► telemetry-dashboard ──► browser
                                                                 └──► telemetry-comparison (on-demand QuixLake query)

ac-telemetry-source ──► ac-telemetry-session ──► session-config-bridge ──► DCM lookup ──► POST to test-manager-backend
                                                                           ▲
                                                          test-manager-frontend ──► test-manager-backend ──► DCM
                                                                                          │
                                                                                          └──► ac-telemetry-config ──► ac-telemetry-lake (enrichment)
```

## Running locally

### Telemetry source (requires Windows + AC)

```bash
cd ac-telemetry-source
pip install -r requirements.txt
python main.py
```

Environment via `.env` (see `.env.example`):
- `Quix__Sdk__Token` — Quix Cloud SDK token (auto-connects QuixStreams)
- `Quix__Portal__Api` — Quix portal API URL
- `SAMPLE_RATE_HZ` — telemetry poll rate (default 60)

### Test Manager stack (cross-platform)

```bash
docker compose -f docker-compose.dev.yml up
```

Brings up MongoDB (27017), mock DCM (8001), backend (8080), frontend (3000) with hot reload. `LOCAL_DEV_MODE=true` and `API_AUTH_ACTIVE=false` skip Quix Portal auth. The mock DCM in `mock_config_api/` implements the DCM OpenAPI spec and is also used by backend tests.

## Tooling

### Backend (Python)
- `uv run ruff check .` — lint
- `uv run ruff format --check .` — format check
- `uv run ty check` — type check (Astral's ty, beta; replaces mypy)
- `uv run pytest` — tests

### Frontend (TypeScript/React)
- `npm run type-check` — `tsc --noEmit`
- `npm run lint` — ESLint (Next.js config)
- `npm run format` — Prettier (with `prettier-plugin-tailwindcss`)
- `npm run build` — production build (required gate before push; `next dev` does not type-check)

## Testing

`test-manager-backend/tests/` is the main suite: pytest + testcontainers (real MongoDB) + in-process DCM mock loaded from `mock_config_api/`. Covers auth, devices, drivers, environments, logbook, tests, validation. `conftest.py` imports the mock via `importlib` and clears state between tests.

Other services (`ac-telemetry-source`, `telemetry-dashboard`, `ac-telemetry-lake`, `session-config-bridge`, `telemetry-comparison`) have no tests — verify manually or via cloud deployment.

## Key implementation details

- **Shared memory structs** (`ac-telemetry-source/models.py`): `ACPhysics`, `ACGraphics`, `ACStatic` are ctypes Structures matching AC's memory layout. **Field order is critical** (sequential memory read). Reference: https://assettocorsa.club/forum/index.php?threads/shared-memory-documentation.3352/
- **Session detection** (`ac-telemetry-source/ac_source.py`): New session = change in `car|track` key. Session ID is a UTC timestamp string; static data published once per session change.
- **Per-wheel fields** use suffix convention: `FL`, `FR`, `RL`, `RR` (e.g., `tyreTempFL`, `brakeTempRR`). Lake partitioning relies on these exact names.
- **Test Manager entities** — Auto-generated prefixed IDs: `DEV-0001` (Device), `DRV-0001` (Driver), `ENV-0001` (Environment), `TST-0001` (Test). Sessions are stored as an array on Test (not their own collection).
- **DCM config routing** — Each test edit creates a new version on the same `config_id` (server-side `replace: true`). Deleting a test deletes only its version, not the whole config. Test target is keyed by hostname; bridge resolves active test via latest version's `test_id`.
- **Quix deployment config** — `quix.yaml` defines deployments, topics, variables, and plugin hooks (embedded view, sidebar items). Each app's `app.yaml` defines its own variables and entry point.
- **In-cluster service discovery** — Backend service names (e.g., `test-manager-backend`) resolve via k8s DNS when `network.serviceName` is declared.

## External dependencies

- **QuixStreams** — Kafka client + Source/Sink framework. https://quix.io/docs for `Application`, `Source`, sinks, `app.yaml`, `quix.yaml`.
- **quixportal** — Blob storage abstraction (S3/Azure/GCP/MinIO) used by the lake sink. Installed from Azure DevOps private index.
- **testcontainers** — Ephemeral MongoDB for backend tests.
