# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AC Quix Bridge is a live telemetry pipeline from **Assetto Corsa** (original) to **Quix Cloud**. It reads AC's Windows shared memory at configurable Hz (default 60), publishes to Kafka via QuixStreams, and feeds downstream consumers for dashboards and data lake storage.

## Architecture

Three Quix deployments defined in `quix.yaml`, each a separate subdirectory with its own `app.yaml`, `dockerfile`, `requirements.txt`, and `main.py`:

1. **ac-telemetry-source** — Custom `QuixStreams.Source` that reads AC shared memory (physics/graphics/static structs via `ctypes` + `mmap`). Produces high-frequency telemetry to `ac-telemetry-raw` topic and session metadata to `ac-telemetry-session` topic on session change. Runs on Windows only (shared memory requirement).

2. **telemetry-dashboard** — FastAPI + WebSocket server. A background thread runs a raw Kafka consumer; messages are broadcast to browser clients via WebSocket. Serves `static/index.html` (Chart.js-based dashboard). Port 80 in Quix Cloud.

3. **ac-telemetry-lake** — Uses `QuixTSDataLakeSink` to write Kafka messages as Hive-partitioned Parquet files to blob storage with optional Iceberg catalog registration. Deployed in Quix Cloud with blob storage binding.

**Data flow:** AC shared memory → ac-telemetry-source → Kafka (`ac-telemetry-raw`) → telemetry-dashboard / ac-telemetry-lake

## Running Locally

```bash
# Source connector (requires Windows + AC running)
cd ac-telemetry-source
pip install -r requirements.txt
python main.py

# Dashboard
cd telemetry-dashboard
pip install -r requirements.txt
uvicorn main:api --host 0.0.0.0 --port 8000
```

Environment variables via `.env` (see `.env.example`):
- `Quix__Sdk__Token` — Quix Cloud SDK token (auto-connects QuixStreams to Kafka)
- `Quix__Portal__Api` — Quix portal API URL
- `SAMPLE_RATE_HZ` — telemetry poll rate (default 60)

## Key Implementation Details

- **Shared memory structs** (`models.py`): `ACPhysics`, `ACGraphics`, `ACStatic` — ctypes Structures matching AC's memory layout. Field order is critical (sequential memory read). Reference: https://assettocorsa.club/forum/index.php?threads/shared-memory-documentation.3352/
- **Session detection** (`ac_source.py`): New session detected by `car|track` key change. Session ID is UTC timestamp string. Static data published once per session change.
- **Per-wheel fields** use suffix convention: `FL`, `FR`, `RL`, `RR` (e.g., `tyreTempFL`, `brakeTempRR`)
- **Quix deployment config**: `quix.yaml` defines deployments, topics, and variables. Each app's `app.yaml` defines its own variables and run entry point.

## External Dependencies

- **QuixStreams** — Kafka client and source/sink framework. Consult https://quix.io/docs for API details on `Application`, `Source`, sinks, `app.yaml`, and `quix.yaml` structure.
- **quixportal** — Blob storage abstraction (S3/Azure/GCP/MinIO) used by the lake sink. Installed from Azure DevOps private index.
