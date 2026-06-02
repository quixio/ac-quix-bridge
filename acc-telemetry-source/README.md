# ACC Telemetry Source

QuixStreams custom Source connector that reads Assetto Corsa Competizione (ACC)
physics telemetry from Windows shared memory and publishes it to Kafka.

ACC reuses AC's three shared-memory region names (`Local\acpmf_physics`,
`Local\acpmf_graphics`, `Local\acpmf_static`) with extended struct layouts.
See `docs/ACCSharedMemoryDocumentationV1.8.12.pdf` for the official Kunos spec.

## Files

- **`main.py`** — Entry point. Creates the QuixStreams Application and wires up the source.
- **`acc_source.py`** — `AssettoCorsaCompetizioneSource` class extending `quixstreams.sources.Source`.
- **`acc_reader.py`** — Opens and reads ACC shared memory via `mmap` + `ctypes`.
- **`models.py`** — `ACCPhysics` / `ACCGraphics` / `ACCStatic` ctypes structs ported from the v1.8.12 SDK.
- **`docs/ACCSharedMemoryDocumentationV1.8.12.pdf`** — Official Kunos ACC SDK doc.

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `Quix__Broker__Address` | Quix broker URL (copy from `ac-telemetry-source/.env`) | — |
| `output` | Kafka topic for physics+graphics | `acc-telemetry-raw` |
| `session_output` | Kafka topic for session metadata | `acc-telemetry-session` |
| `SAMPLE_RATE_HZ` | Telemetry polling rate (ACC graphics block updates at ~60 Hz) | `50` |

## Running locally

Windows + ACC required (shared memory is OS-specific).

```powershell
cd acc-telemetry-source
pip install -r requirements.txt
python main.py
```

Or via the repo-root startup script (after one is added for ACC).
