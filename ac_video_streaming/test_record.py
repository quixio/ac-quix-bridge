"""
Standalone recording test — no Kafka, no QuixStreams needed.

Captures your screen using dxcam with mock AC session lifecycle:
  - Records per-lap MP4 files to ./recordings/
  - Simulates lap changes, pause, and session end

Run:
  .venv\Scripts\python test_record.py

MP4 files will be in: ac_video_streaming/recordings/
"""

import logging
import os
import time
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    from ac_reader_mock import ACGraphicsReaderMock
    from video_recorder import VideoRecorder

    # Initialize
    fps = int(os.environ.get("VIDEO_FPS", "30"))
    display_index = int(os.environ.get("VIDEO_DISPLAY_INDEX", "0"))
    output_dir = os.environ.get("VIDEO_OUTPUT_DIR", "./recordings")
    recording_width = int(os.environ.get("RECORDING_WIDTH", "1920"))

    logger.info("=== AC Video Recording Test ===")
    logger.info("FPS: %d | Display: %d | Output: %s", fps, display_index, os.path.abspath(output_dir))

    # Initialize dxcam
    import dxcam
    logger.info("Initializing screen capture on display %d...", display_index)
    camera = dxcam.create(output_idx=display_index)
    frame = camera.grab()
    if frame is None:
        logger.error("Failed to grab frame. Is this a real desktop session (not RDP)?")
        return
    h, w = frame.shape[:2]
    logger.info("Screen capture ready: %dx%d", w, h)

    # Initialize recorder and mock reader
    recorder = VideoRecorder(output_dir, fps, recording_width)
    reader = ACGraphicsReaderMock()
    reader.open()

    prev_status = None
    prev_completed_laps = None
    prev_current_time = None
    session_id = None
    interval = 1.0 / fps
    next_tick = time.perf_counter()
    recorded_files = []

    logger.info("")
    logger.info("Recording started! Will run through one mock session cycle (~76 seconds).")
    logger.info("Press Ctrl+C to stop early.")
    logger.info("")

    try:
        cycle_count = 0
        while cycle_count < 1:
            next_tick += interval

            gfx = reader.read_graphics()
            status = gfx["status"]
            completed_laps = gfx["completedLaps"]
            current_time = gfx["iCurrentTime"]

            # --- State machine (same as video_source.py) ---

            if status == "live":
                # New session detection
                new_session = False
                if prev_status != "live":
                    if prev_status is None or prev_status in ("off", "replay"):
                        new_session = True
                    elif prev_status == "pause":
                        if prev_current_time is not None and current_time < prev_current_time:
                            new_session = True
                        else:
                            recorder.resume()
                            logger.info(">> RESUMED recording")

                if new_session:
                    if recorder.is_recording:
                        path = recorder.finish_lap()
                        if path:
                            recorded_files.append(path)
                    session_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                    static = reader.read_static()
                    logger.info(">> NEW SESSION: %s (%s @ %s)", session_id, static["carModel"], static["track"])
                    prev_completed_laps = completed_laps
                    recorder.start_lap(session_id, completed_laps, w, h)

                # Lap change
                if not new_session and prev_completed_laps is not None and completed_laps > prev_completed_laps:
                    path = recorder.finish_lap()
                    if path:
                        recorded_files.append(path)
                    logger.info(">> LAP %d COMPLETE", prev_completed_laps)
                    recorder.start_lap(session_id, completed_laps, w, h)
                    prev_completed_laps = completed_laps

                # Capture + record
                frame = camera.grab()
                if frame is not None and recorder.is_recording:
                    recorder.write_frame(frame)

            elif status == "pause" and prev_status == "live":
                recorder.pause()
                logger.info(">> PAUSED")

            elif status == "off" and prev_status and prev_status != "off":
                if recorder.is_recording:
                    path = recorder.finish_lap()
                    if path:
                        recorded_files.append(path)
                logger.info(">> SESSION ENDED")
                session_id = None
                prev_completed_laps = None
                cycle_count += 1

            prev_status = status
            prev_current_time = current_time

            now = time.perf_counter()
            if next_tick > now:
                time.sleep(next_tick - now)

    except KeyboardInterrupt:
        logger.info("\nStopped by user.")
        if recorder.is_recording:
            path = recorder.finish_lap()
            if path:
                recorded_files.append(path)

    # Summary
    abs_output = os.path.abspath(output_dir)
    logger.info("")
    logger.info("=" * 60)
    logger.info("TEST COMPLETE")
    logger.info("=" * 60)
    logger.info("")
    logger.info("MP4 files saved to: %s", abs_output)
    logger.info("")
    if recorded_files:
        for f in recorded_files:
            size_mb = os.path.getsize(f) / (1024 * 1024) if os.path.exists(f) else 0
            logger.info("  %s (%.1f MB)", os.path.basename(f), size_mb)
    else:
        logger.info("  (no files — test may have been stopped too early)")
    logger.info("")
    logger.info("Open in file explorer:")
    logger.info("  explorer \"%s\"", abs_output)


if __name__ == "__main__":
    main()
