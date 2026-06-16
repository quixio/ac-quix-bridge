# AC Quix Bridge

Live telemetry bridge from **Assetto Corsa** (original) to **Quix Cloud** via shared memory.

## Architecture

```
┌──────────────┐   shared memory    ┌───────────────────┐   Kafka    ┌────────────┐
│ Assetto Corsa│ ─────────────────► │ ac-telemetry-src  │ ────────► │ Quix Cloud │
│  (Windows)   │  Local\acpmf_physics│  (Python Source)  │           │  Pipeline  │
└──────────────┘                    └───────────────────┘           └────────────┘
```

The Python source connector reads AC's physics shared memory at a configurable sample rate (default 60 Hz) and publishes telemetry messages to a Kafka topic managed by Quix Cloud.

## Telemetry Channels

| Channel | Keys |
|---|---|
| Speed | `speedKmh` |
| Gear | `gear` |
| G-forces | `accG_x`, `accG_y`, `accG_z` |
| Tyre temps | `tyreTempFL`, `tyreTempFR`, `tyreTempRL`, `tyreTempRR` |
| Brake temps | `brakeTempFL`, `brakeTempFR`, `brakeTempRL`, `brakeTempRR` |

## Prerequisites

- **Windows** (shared memory is Windows-only)
- **Python 3.12+**
- **Assetto Corsa** installed and running
- **Quix Cloud** account with an SDK token

## Setup

```bash
git clone <repo-url> ac-quix-bridge
cd ac-quix-bridge
python -m venv .venv
.venv\Scripts\activate
pip install -r ac-telemetry-source/requirements.txt
```

## Configuration

Copy the example env file and fill in your Quix Cloud credentials:

```bash
copy .env.example .env
```

Edit `.env`:

```
Quix__Sdk__Token=<your Quix Cloud SDK token>
Quix__Portal__Api=https://portal-api.platform.quix.io
SAMPLE_RATE_HZ=60
```

The `Quix__Sdk__Token` and `Quix__Portal__Api` variables are automatically picked up by QuixStreams to connect to your Quix Cloud workspace.

## Running Locally

```bash
cd ac-telemetry-source
python main.py
```

**Important:** Assetto Corsa must be running and in an **active driving session** for shared memory to be populated. If AC is not running, the source will log a warning and retry every 5 seconds automatically.

## Deploying to Quix Cloud

1. Connect this Git repository to your Quix Cloud project
2. Quix will read `quix.yaml` and set up the pipeline automatically
3. Configure the `output` topic variable and your SDK token in the Quix Cloud UI
4. Deploy — the service will start reading telemetry when AC is running on the host machine

> **Note:** Since AC shared memory is only available on the Windows machine running the game, this source is typically run locally (not in Quix Cloud containers). Use Quix Cloud for downstream processing, dashboards, and storage.
