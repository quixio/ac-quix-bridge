"""
Test script for session restart detection in Assetto Corsa.

Run on Windows with AC open. Reads shared memory at 1Hz and logs
all fields that could change on session restart. Compares every tick
with the previous tick and highlights any changes.

No Kafka/Quix dependency — pure shared memory reading.

Usage:
    cd ac-telemetry-source
    pip install -r requirements.txt
    python test_session_detect.py
"""

import logging
import time

from ac_reader import ACReader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0  # seconds

# Fields to track for changes — from physics, graphics, and static
GRAPHICS_FIELDS = [
    "status",
    "sessionType",
    "completedLaps",
    "distanceTraveled",
    "normalizedCarPosition",
    "iCurrentTime",
    "iLastTime",
    "iBestTime",
    "currentSectorIndex",
    "lastSectorTime",
    "numberOfLaps",
    "isInPit",
    "isInPitLane",
    "penaltyTime",
    "flag",
    "tyreCompound",
]

PHYSICS_FIELDS = [
    "packetId",
    "speedKmh",
    "fuel",
    "performanceMeter",
    "gear",
]

STATIC_FIELDS = "ALL"  # Track every field from read_static()


def main():
    reader = ACReader()

    prev_graphics = {}
    prev_physics = {}
    prev_static = {}
    prev_session_key = None
    tick = 0

    logger.info("Waiting for AC shared memory...")

    while True:
        if not reader.is_open:
            try:
                reader.open()
                logger.info("Connected to AC shared memory")
            except FileNotFoundError:
                time.sleep(5)
                continue

        try:
            data = reader.read_physics_and_graphics()
            static = reader.read_static()
            session_key = reader.get_session_key()

            # Extract tracked fields
            cur_graphics = {f: data.get(f) for f in GRAPHICS_FIELDS}
            cur_physics = {f: data.get(f) for f in PHYSICS_FIELDS}
            cur_static = static  # Track ALL static fields

            # Find changes
            changes = []

            if prev_session_key is not None and session_key != prev_session_key:
                changes.append(f"  car|track: {prev_session_key} -> {session_key}")

            for name, cur, prev in [
                ("graphics", cur_graphics, prev_graphics),
                ("physics", cur_physics, prev_physics),
                ("static", cur_static, prev_static),
            ]:
                for field, val in cur.items():
                    old = prev.get(field)
                    if old is not None and old != val:
                        # For floats, show with precision
                        if isinstance(val, float):
                            changes.append(f"  {name}.{field}: {old:.2f} -> {val:.2f}")
                        else:
                            changes.append(f"  {name}.{field}: {old} -> {val}")

            # Log changes if any non-trivial ones exist
            # Filter out noisy fields that change every tick during normal driving,
            # but detect significant drops (resets) in continuously-increasing fields
            NOISY_FIELDS = {
                "packetId", "iCurrentTime", "normalizedCarPosition",
                "distanceTraveled", "speedKmh", "performanceMeter",
                "currentSectorIndex", "gear",
            }

            # Detect resets in fields that normally increase
            reset_changes = []
            for field, threshold in [
                ("iCurrentTime", 5000),      # lap time dropped by > 5s
                ("distanceTraveled", 100),    # distance dropped by > 100m
                ("iLastTime", 5000),          # last lap time changed significantly
                ("iBestTime", 5000),          # best lap time changed significantly
            ]:
                old = prev_graphics.get(field)
                new = cur_graphics.get(field)
                if old is not None and new is not None and (old - new) > threshold:
                    reset_changes.append(f"  RESET graphics.{field}: {old} -> {new}")

            significant_changes = [
                c for c in changes
                if not any(noisy in c for noisy in NOISY_FIELDS)
            ] + reset_changes

            if significant_changes:
                logger.info("=" * 60)
                logger.info(">>> SIGNIFICANT CHANGES DETECTED <<<")
                for c in significant_changes:
                    logger.info(c)
                logger.info("=" * 60)

            # Always log a status line
            logger.info(
                "tick=%04d  status=%-7s  laps=%d  dist=%8.1f  "
                "iCur=%d  iLast=%d  iBest=%d  "
                "fuel=%.1f  nSessions=%d  pos=%.4f  "
                "key=%s",
                tick,
                cur_graphics["status"],
                cur_graphics["completedLaps"],
                cur_graphics["distanceTraveled"],
                cur_graphics["iCurrentTime"],
                cur_graphics["iLastTime"],
                cur_graphics["iBestTime"],
                cur_physics["fuel"],
                cur_static["numberOfSessions"],
                cur_graphics["normalizedCarPosition"],
                session_key,
            )

            # Also log ALL changes (including noisy ones) for full visibility
            if changes:
                logger.debug("All changes this tick:")
                for c in changes:
                    logger.debug(c)

            # Update previous state
            prev_graphics = cur_graphics
            prev_physics = cur_physics
            prev_static = cur_static
            prev_session_key = session_key
            tick += 1

        except Exception:
            logger.exception("Error reading shared memory, reconnecting...")
            reader.close()
            prev_graphics = {}
            prev_physics = {}
            prev_static = {}
            prev_session_key = None
            time.sleep(5)
            continue

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
