# AC Quix Bridge — Product Overview

A plain-language description of what the product does today and how its pieces fit together.

## What problem it solves

A racing team runs laps in Assetto Corsa and wants to learn from each session. The product captures what happens during the race — telemetry data (speed, throttle, tyres, brakes, suspension, …) and video — ships it all to the cloud, and gives the team tools to watch live, review recordings, and compare laps to understand where time is won and lost on track.

## The two sides of the product

### 1. The racing machine (Windows PC running Assetto Corsa)

This is where the data is born. The AC game exposes a shared-memory interface with hundreds of live signals, and the game screen itself shows what the driver sees. The product runs two small services on this PC:

- **Telemetry Source** — reads the shared memory 60 times per second and sends every signal to Quix Cloud
- **Video Streaming** — captures the game screen in real time, streams it live as a video feed, records each lap as an MP4 file, and uploads each finished lap to cloud storage

Both services start with a single script (`start-local.ps1`) and react to what the game does: recording waits for the car to cross the start/finish line (so the MP4 has no pitstop footage), pauses when the game pauses, splits files at every lap, and finalizes cleanly when the session ends or the game closes.

### 2. Quix Cloud (the analytics platform)

This is where the data lives and where the team consumes it. Everything runs as a service in Quix Cloud, accessible through public URLs in any browser:

- **Live Video Viewer** — watch the race in real time, from anywhere, no install needed
- **Video Browser** — browse all recorded sessions, preview any lap video in-browser, download individual laps or whole sessions
- **Telemetry Explorer** — the analytics workbench: select sessions and laps, overlay their data, compare performance across drivers/cars/runs
- **Raw Data Visualization** (Marimo notebook) — interactive Python notebook for deeper custom analysis
- **Data Lake** — every telemetry sample lands in cloud storage as Parquet (columnar, queryable with SQL) and every video lap MP4 sits alongside it

## What the analyst sees — Telemetry Explorer

This is the main tool for comparing laps and spotting performance differences.

### Picking what to compare

The team's data is organized by a hierarchy:

```
Environment → Test Rig → Experiment → Driver → Track → Car → Session → Lap
```

You can add one or more "rows" and for each row pick any level of detail. Every lap you tick gets its own colored trace. You can overlay:

- Two laps from the same driver on the same car to see consistency
- Different drivers on the same car/track to compare driving styles
- Same driver across different cars to isolate car setup differences
- Practice vs qualifying vs race laps to see how strategy changes behavior

### Picking what to plot

All telemetry channels are grouped into categories: Inputs (throttle, brake, steering, gear), Motion (speed, velocities, G-forces, heading, pitch, roll), Engine (RPM, turbo, fuel, DRS, KERS/ERS), Tyres (temperatures — inner/middle/outer/core — pressures, wear, slip, load, camber), Suspension & Brakes, Environment (air/road temp, grip), Car State (damage, TC, ABS, pit limiter), Session (lap time, position, sector times).

Click any channel chip to turn it into a plot. Each plot stacks vertically and spans the full width.

### The track map (top-right, always visible)

A 2D map of the circuit is pinned in the top-right corner and stays visible while you scroll through plots. It's built from a CSV that describes the track as ~2,300 points (x, y, z coordinates, distance, corner radius, ideal speed, width). The map shows:

- The **track shape**, colored by how tight each section is:
  - **Red** — hairpin (radius under 60m)
  - **Orange** — tight corner (60–150m)
  - **Yellow** — sweeper (150–400m)
  - **Green** — straight (400m or more)
- **Corner labels** T1, T2, T3, … automatically placed at the middle of each corner
- **Start/Finish marker** at the start of the lap
- A **red dot** showing where on the track the analyst is currently looking
- A **zoom slider** from 1× to 8×. At 1× the full track is always visible. Above 1×, the view automatically follows the red dot so you can examine a specific corner in detail while scrubbing the plot marker

Thresholds and colors are editable in `tracks_config.json`, so the team can tune what counts as a hairpin vs a tight corner. Mouse zoom/pan on the map is disabled — the slider is the sole zoom control.

### The synced position marker

Every plot has a thin vertical line at the current track position. The line can be **dragged with the mouse on any plot** — and when dragged, all other plots and the red dot on the track update together, instantly. This turns the whole screen into a synchronized analysis tool: point at a speed dip on one plot and immediately see where on the track that happens, what the throttle and brake were doing at the same moment, and what the tyre temperatures looked like.

The marker position persists across changes — if you re-select laps or change signals, the line stays where you put it, so you can iterate without losing your place.

### The value readout

Values are shown in two places:

- **On every plot, next to the marker**: up to 6 values per plot stacked vertically in a fixed column pinned to the top of the plot. Each label is boxed in its trace's color so the analyst can instantly tell which lap each value belongs to. The labels never overlap regardless of where the actual trace points fall. If more than 6 traces are overlaid, a "+N" badge appears at the bottom of the stack.
- **In the track panel**: the current track position (percentage around the lap + distance in meters).

Values are interpolated between data points for smooth readout.

### Corner overlays per plot

Next to each plot title is a **"Show corners" checkbox**. When checked, the plot gets vertical shaded bands where the track has corners, colored the same way as the track map (red/orange/yellow). Each band is labeled T1, T2, … at the top. This makes it obvious which braking event corresponds to which corner, and which corner has the biggest speed delta between two laps.

You toggle this per plot — so you can have corners visible on the Speed plot for context but keep your Tyre Temperature plot clean.

### Synced video playback

Below the track map there's a **video panel**. When you click Plot with at least one lap checked, a dropdown appears listing every currently-plotted lap. The first lap's MP4 loads automatically; switching the dropdown loads a different one. Only one video plays at a time — if you've overlaid three laps, you pick which lap's camera feed drives the scrub.

Once a video is loaded, the marker and the video are synchronized in two modes:

- **Playing the video** (pressing the native play control): the video drives the marker. The red dot on the track and the vertical line on every plot follow the video frame-by-frame.
- **Paused video**: the marker drives the video. Drag the red line on any plot and the video seeks to the matching frame. Grabbing the marker while the video is playing will pause it and take control — there's no fight between the two.

Under the hood each MP4 has a companion `*.sync.json` sidecar in the same S3 folder. The sidecar carries a sub-sampled map of video frame → wall-clock → `normalizedCarPosition`. When you open the Explorer, the Explorer fetches the sidecar, builds two lookup tables in the browser, and uses them to translate back and forth between the video timeline and the track position the plots care about.

If a lap's video was recorded before sidecars existed, or if the telemetry source wasn't running when the video was recorded (so the session ids don't line up), the panel shows a message saying sync isn't available — the plots still work as before, just without a video.

## The data lake — behind the scenes

Every telemetry sample is written continuously to cloud object storage as Hive-partitioned Parquet files (organized by environment / rig / experiment / driver / track / car / session / year / month / day / hour / lap). This makes it:

- **Queryable with SQL** via DuckDB — fast, ad-hoc, no setup
- **Catalogued** via an Iceberg REST catalog for structured access
- **Compatible** with any data tool that reads Parquet (Python, R, Tableau, PowerBI, …)

Video recordings are uploaded to the same blob storage bucket under a `ac_video/` prefix, organized by session ID and lap number. They don't sit in the structured data lake — they're just files, but the Video Browser web app lists them, lets you preview them in the browser, and download them.

## What happens during a typical race

1. The driver sits down at the AC machine and launches the game
2. An operator runs `start-local.ps1` — two terminal windows open, one for telemetry, one for video
3. The driver picks their car and track, goes out on track
4. The moment the car **crosses the start/finish line**, both services start producing data:
   - Telemetry goes to Kafka → data lake in real time
   - Video is captured at 30 FPS locally + streamed live at 15 FPS to Kafka
5. A **team member anywhere in the world** can open the live video viewer in a browser and watch the session unfold
6. As each **lap completes**, the current MP4 is finalized, uploaded to cloud storage, and the local file is deleted
7. If the game is **paused** (menu opens, pit stop), both telemetry and video pause cleanly — no noise in the data
8. When the session **ends** (OFF status or game closes), the last partial recording is still saved and uploaded — no data is lost
9. **Back at the office**, analysts open the Telemetry Explorer, pick the new session's laps, overlay them on previous attempts, drag the marker through the lap, see corners highlighted, and identify where improvement is possible

## Configuration

The whole system is driven by a few configuration files:

- `.env` files with tokens and connection strings (one per service, kept out of git)
- `tracks_config.json` for corner classification thresholds and colors
- `tracks/<track_name>/layout_*.csv` for each track's geometry
- `channels.json` for the label, unit, and category of every telemetry signal
- `quix.yaml` describing all cloud deployments, their resources, and their environment variables

This means non-developers can tune the product — e.g. change what counts as a "tight" vs "sweeper" corner — without touching code.

## What's intentionally not in yet

- **Multi-track switching** — the Telemetry Explorer currently hard-codes one track CSV (Nürburgring Sprint A). Auto-selection based on the session's `track` field is planned but not yet active.
- **Corner classification tuning per track** — the same thresholds apply to all tracks. A per-track override in the config is a natural next step.
- **HTTP Range requests for the proxied MP4** — the Telemetry Explorer now supports HTTP Range seeking. Videos up to 100 MB are fully blob-buffered in the browser for instant seeking; larger files fall back to range-based streaming.

## Stack summary

- **Language:** Python 3.12 everywhere
- **Messaging:** Apache Kafka (via Quix Cloud and QuixStreams)
- **Storage:** S3-compatible blob (eu-west-2) — raw files + Parquet data lake + Iceberg catalog
- **Query:** DuckDB (in-process SQL) reading Parquet
- **Video:** dxcam (DirectX screen capture on Windows) + FFmpeg (H.264 MP4 encoding) + JPEG frames over Kafka for live view
- **Web UI:** FastAPI + Plotly.js (Telemetry Explorer, Video Viewer, Video Browser)
- **Notebooks:** Marimo (Raw Data Visualization)
- **Deployment:** Quix Cloud (Linux containers) + a local Windows runner for the AC capture services

---

This document is intentionally non-technical. For API endpoints, file paths, environment variables, and deployment details see `docs/video-streaming-user-guide.md` and `docs/video-streaming-answers.md`.
