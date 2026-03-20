# AC Telemetry Source

QuixStreams custom Source connector that reads Assetto Corsa physics telemetry from Windows shared memory and publishes it to a Kafka topic.

## Files

- **`main.py`** — Entry point. Creates the QuixStreams Application and wires up the source.
- **`ac_source.py`** — `AssettoCorsaSource` class extending `quixstreams.sources.Source`.
- **`ac_reader.py`** — Opens and reads the AC shared memory map via `mmap` + `ctypes`.
- **`models.py`** — `ACPhysics` ctypes struct matching the official AC physics shared memory layout.

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `output` | Kafka topic name (set by Quix) | `ac-telemetry-raw` |
| `SAMPLE_RATE_HZ` | Telemetry polling rate in Hz | `60` |
