"""
Test script for session restart detection in Assetto Corsa.

Run on Windows with AC open. Reads shared memory at 1Hz and logs
fields relevant to detecting session restarts:
  - status (AC_OFF=0, AC_REPLAY=1, AC_LIVE=2, AC_PAUSE=3)
  - completedLaps
  - distanceTraveled
  - packetId (physics + graphics)
  - car|track key

When a potential restart is detected, logs the reason.
No Kafka/Quix dependency — pure shared memory reading.

Usage:
    cd ac-telemetry-source
    pip install -r requirements.txt
    python test_session_detect.py
"""

import ctypes
import logging
import time

from ac_reader import ACReader, STATUS_TYPES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0  # seconds


def main():
    reader = ACReader()

    # Previous state for change detection
    prev_status = None
    prev_completed_laps = None
    prev_distance = None
    prev_physics_packet_id = None
    prev_session_key = None

    logger.info("Waiting for AC shared memory...")

    while True:
        # Connect if not open
        if not reader.is_open:
            try:
                reader.open()
                logger.info("Connected to AC shared memory")
            except FileNotFoundError:
                time.sleep(5)
                continue

        try:
            data = reader.read_physics_and_graphics()
            session_key = reader.get_session_key()

            status = data["status"]
            completed_laps = data["completedLaps"]
            distance = data["distanceTraveled"]
            physics_packet_id = data["packetId"]

            # --- Detect restart signals ---
            reasons = []

            if prev_session_key is not None and session_key != prev_session_key:
                reasons.append(f"car|track changed: {prev_session_key} -> {session_key}")

            if prev_status is not None and prev_status != "live" and status == "live":
                reasons.append(f"status went {prev_status} -> live")

            if prev_completed_laps is not None and completed_laps < prev_completed_laps:
                reasons.append(f"completedLaps dropped: {prev_completed_laps} -> {completed_laps}")

            if prev_distance is not None and prev_distance > 100 and distance < 10:
                reasons.append(f"distanceTraveled reset: {prev_distance:.0f} -> {distance:.0f}")

            if prev_physics_packet_id is not None and physics_packet_id < prev_physics_packet_id:
                reasons.append(f"packetId dropped: {prev_physics_packet_id} -> {physics_packet_id}")

            if reasons:
                logger.info("=" * 60)
                logger.info(">>> NEW SESSION DETECTED <<<")
                for r in reasons:
                    logger.info("  reason: %s", r)
                logger.info("=" * 60)

            # --- Log current state ---
            logger.info(
                "status=%-7s  laps=%d  distance=%8.1f  packetId=%d  key=%s",
                status, completed_laps, distance, physics_packet_id, session_key,
            )

            # Update previous state
            prev_status = status
            prev_completed_laps = completed_laps
            prev_distance = distance
            prev_physics_packet_id = physics_packet_id
            prev_session_key = session_key

        except Exception:
            logger.exception("Error reading shared memory, reconnecting...")
            reader.close()
            prev_status = None
            prev_completed_laps = None
            prev_distance = None
            prev_physics_packet_id = None
            prev_session_key = None
            time.sleep(5)
            continue

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
