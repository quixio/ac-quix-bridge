"""
Capture preview — grabs a single frame using the exact same display/region
resolution logic as main.py and saves it as ./capture_preview.png so you can
verify the right monitor (and crop, if any) is selected before recording.

Usage:
  python preview_capture.py            # preview currently configured display
  python preview_capture.py --all      # one PNG per enumerated display
"""

import os
import sys
from pathlib import Path

import cv2
from dotenv import load_dotenv

# ENV_FILE is mandatory: it selects the target environment (env/.env.byox or
# env/.env.quixdev).
_env_file = os.environ.get("ENV_FILE")
if not _env_file or not Path(_env_file).is_file():
    raise SystemExit(
        "ENV_FILE is not set or points to a missing file. "
        "Launch via startUpScript-acc.bat (environment selector) or set ENV_FILE "
        r"to e.g. C:\repos\ac-quix-bridge\env\.env.quixdev"
    )
load_dotenv(_env_file)

# Reuse the resolution logic from the production source so the preview
# matches exactly what recording will see.
from video_source import ACVideoSource


PREVIEW_MAX_WIDTH = 1600  # downscale so the saved PNG opens fast


def _save_preview(frame, out_path: Path):
    h, w = frame.shape[:2]
    if w > PREVIEW_MAX_WIDTH:
        scale = PREVIEW_MAX_WIDTH / w
        frame = cv2.resize(frame, (PREVIEW_MAX_WIDTH, int(h * scale)))
    # dxcam returns RGB; cv2 expects BGR for imwrite.
    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(out_path), bgr)
    print(f"Saved: {out_path}  ({w}x{h} captured)")


def preview_current():
    src = ACVideoSource(name="preview")
    camera, size = src._init_camera()
    if camera is None:
        print("Failed to initialize camera — see logs above.", file=sys.stderr)
        sys.exit(1)
    frame = camera.grab()
    if frame is None:
        print("Camera returned no frame.", file=sys.stderr)
        sys.exit(1)
    out = Path(__file__).resolve().parent / "capture_preview.png"
    _save_preview(frame, out)
    try:
        os.startfile(str(out))  # Windows: open the PNG in the default viewer
    except Exception:
        pass


def preview_all():
    import dxcam
    src = ACVideoSource(name="preview")
    outputs = src._enumerate_outputs(dxcam)
    if not outputs:
        print("No displays enumerated.", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(__file__).resolve().parent
    for o in outputs:
        try:
            cam = dxcam.create(device_idx=o["device"], output_idx=o["output"])
            frame = cam.grab()
            if frame is None:
                print(f"  device={o['device']} output={o['output']} → no frame")
                continue
            tag = f"d{o['device']}_o{o['output']}_{o['resolution'][0]}x{o['resolution'][1]}"
            _save_preview(frame, out_dir / f"capture_preview_{tag}.png")
        except Exception as e:
            print(f"  device={o['device']} output={o['output']} → error: {e}")
        finally:
            try:
                cam.release()
            except Exception:
                pass


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        preview_all()
    else:
        preview_current()
