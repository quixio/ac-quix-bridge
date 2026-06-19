"""
Lap Record Detector

Consumes ac-telemetry-raw, enriches with DCM config (driver, track, carModel),
and emits one message to `lap-records` whenever a driver sets a new personal
best lap time on a given track+car combination.

Lap boundary detection:
  - completedLaps increments by 1 each time a lap crosses the finish line
  - iLastTime (ms) holds the time of the just-completed lap
  - Sentinel values: 0 (AC) and INT32_MAX (ACC) mean no valid time

State (RocksDB, per Kafka message key = source PC hostname):
  - "last_completed|{driver}|{track}|{car}" -> int: last seen completedLaps value
  - "best_ms|{driver}|{track}|{car}"        -> int: best lap time in ms
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOGLEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

INT32_MAX = 2_147_483_647  # ACC sentinel for missing lap time


def detect_lap_record(row: dict, state) -> dict | None:
    """
    Called for every enriched telemetry row.
    Returns a lap-record dict when a new personal best is set, else None.
    """
    driver = row.get("driver", "NA")
    track = row.get("track", "NA")
    car = row.get("carModel", "NA")

    # Skip rows where enrichment hasn't arrived yet
    if driver == "NA" or track == "NA" or car == "NA":
        return None

    completed_laps = row.get("completedLaps", -1)
    if not isinstance(completed_laps, int) or completed_laps < 0:
        return None

    combo = f"{driver}|{track}|{car}"
    laps_key = f"last_completed|{combo}"
    best_key = f"best_ms|{combo}"

    last_completed = state.get(laps_key, default=-1)

    result = None

    # Lap boundary: completedLaps just incremented
    if completed_laps > last_completed and last_completed >= 0:
        i_last_time = row.get("iLastTime", 0)

        # Validate: reject sentinel values (0 = no time, INT32_MAX = ACC missing)
        if isinstance(i_last_time, int) and 0 < i_last_time < INT32_MAX:
            current_best = state.get(best_key, default=INT32_MAX)

            if i_last_time < current_best:
                logger.info(
                    "New lap record! driver=%s track=%s car=%s lap=%d time=%dms (prev best=%s)",
                    driver, track, car, completed_laps,
                    i_last_time,
                    f"{current_best}ms" if current_best < INT32_MAX else "none",
                )
                result = {
                    "session_id": row.get("session_id"),
                    "driver": driver,
                    "track": track,
                    "carModel": car,
                    "environment": row.get("environment", "NA"),
                    "test_rig": row.get("test_rig", "NA"),
                    "experiment": row.get("experiment", "NA"),
                    "test_id": row.get("test_id", "NA"),
                    "lap_number": completed_laps,
                    "lap_time_ms": i_last_time,
                    "lap_time_display": row.get("lastTime", ""),
                    "previous_best_ms": current_best if current_best < INT32_MAX else None,
                    "improvement_ms": (current_best - i_last_time) if current_best < INT32_MAX else None,
                    "timestamp_ms": row.get("timestamp_ms"),
                }
                state.set(best_key, i_last_time)

    # Always persist the latest completedLaps so we can detect the next boundary
    state.set(laps_key, completed_laps)
    return result


if __name__ == "__main__":
    from quixstreams import Application
    from quixstreams.dataframe.joins.lookups import QuixConfigurationService

    app = Application(
        consumer_group=os.getenv("CONSUMER_GROUP", "lap-record-detector"),
        auto_offset_reset=os.getenv("AUTO_OFFSET_RESET", "earliest"),
    )

    input_topic = app.topic(os.environ["input"], key_deserializer="str", value_deserializer="json")
    output_topic = app.topic(os.environ["output"], value_serializer="json")

    config_topic = app.topic(os.getenv("config_topic", "ac-telemetry-config"))
    config_lookup = QuixConfigurationService(
        topic=config_topic,
        app_config=app.config,
    )

    sdf = app.dataframe(topic=input_topic)

    # Enrich with experiment config (driver, environment, test_rig, experiment_id, test_id)
    # and session config (carModel, track) — mirrors ac-telemetry-lake enrichment exactly
    sdf = sdf.join_lookup(
        lookup=config_lookup,
        fields={
            "test_id":     config_lookup.json_field(jsonpath="$.test_id",       type="experiment", default="NA"),
            "environment": config_lookup.json_field(jsonpath="$.environment",   type="experiment", default="NA"),
            "test_rig":    config_lookup.json_field(jsonpath="$.test_rig",      type="experiment", default="NA"),
            "experiment":  config_lookup.json_field(jsonpath="$.experiment_id", type="experiment", default="NA"),
            "driver":      config_lookup.json_field(jsonpath="$.driver",        type="experiment", default="NA"),
            "carModel":    config_lookup.json_field(jsonpath="$.carModel",      type="session",    default="NA"),
            "track":       config_lookup.json_field(jsonpath="$.track",         type="session",    default="NA"),
        },
    )

    # Stateful lap-record detection
    sdf = sdf.apply(detect_lap_record, stateful=True)

    # Drop rows where no new record was set (apply returned None)
    sdf = sdf.filter(lambda x: x is not None)

    sdf = sdf.to_topic(output_topic)

    logger.info("Starting Lap Record Detector")
    logger.info("  Input topic : %s", os.environ["input"])
    logger.info("  Config topic: %s", os.getenv("config_topic", "ac-telemetry-config"))
    logger.info("  Output topic: %s", os.environ["output"])
    app.run()
