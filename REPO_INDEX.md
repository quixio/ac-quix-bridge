# Repo index

> Lookup-only map of this repo for Claude, other LLMs, and humans orienting
> fast. Not a tutorial. Pairs with:
>
> - `CLAUDE.md` — prose architecture and conventions.
> - `.claude/repo-index.json` — machine-generated symbol/route index. Regenerate
>   with `python scripts/gen_repo_index.py`. Check freshness with
>   `python scripts/gen_repo_index.py --check` (exit 1 if stale).
> - `.claude/skills/` — task playbooks (video-seeking, video-capture, ...).
> - `~/.claude/agents/devTeam-*.md` — agent roster (spec → code → test → docs).

Regenerate the JSON after adding/moving top-level dirs or renaming public
symbols. The generator is stdlib-only and runs in seconds.

---

## Top-level services

| Dir                        | Kind                 | Entry     | Purpose                                                                                              |
|----------------------------|----------------------|-----------|------------------------------------------------------------------------------------------------------|
| `ac-telemetry-source/`     | QuixStreams source   | `main.py` | Reads AC shared memory (physics/graphics/static) and publishes to Kafka. Windows-only; runs on sim PC. |
| `ac-telemetry-lake/`       | QuixStreams sink     | `main.py` | `QuixTSDataLakeSink` — writes Kafka messages as Hive-partitioned Parquet to blob storage.            |
| `telemetry-dashboard/`     | FastAPI + WebSocket  | `main.py` | Live single-session dashboard. Kafka consumer thread fans out to browser WS clients (Chart.js SPA).   |
| `telemetry-comparison/`    | FastAPI + static SPA | `main.py` | Telemetry Explorer. QuixLake-backed lap comparison UI, embedded as Test Manager Analysis iframe.      |
| `session-config-bridge/`   | QuixStreams app      | `main.py` | Consumes `ac-telemetry-session`, pushes config to DCM + POSTs sessions to test-manager-backend.       |
| `test-manager-backend/`    | FastAPI + MongoDB    | `main.py` | CRUD for Devices, Drivers, Environments, Tests, Logbook. 50+ routes in `api/routes/`.                 |
| `test-manager-frontend/`   | Next.js 14 / TS      | —         | Portal-embeddable UI. Analysis tab iframes Telemetry Explorer. **Not yet indexed in JSON (TS).**      |
| `ac_video_streaming/`      | QuixStreams source   | `main.py` | Per-lap MP4 capture via ffmpeg + sidecar JSON + S3 upload. Disabled by default.                      |
| `ac-video-viewer/`         | FastAPI              | `main.py` | Kafka frame viewer. Disabled by default.                                                             |
| `ac-video-browser/`        | FastAPI              | `main.py` | Blob-storage browser for recorded MP4s.                                                              |
| `mongodb/`                 | Deployment stub      | —         | Shared MongoDB — two databases: `test_manager` and `ac_telemetry`.                                   |
| `mongodb-backup-manager/`  | Utility              | —         | Mongo backup tooling.                                                                                |
| `mock_config_api/`         | Test fixture         | —         | In-process DCM mock. Used by `test-manager-backend/tests/` and the local dev compose.                |

Not services: `dev-planning/` (planning scratch, gitignored), `docs/`, `.claude/`,
`.tmp/` (scratch, gitignored), `ac-machine-secrets/` (gitignored).

---

## Telemetry Explorer JS modules

Frontend lives at `telemetry-comparison/static/`. Loaded as ES modules from
`index.html`. Module-graph and export lists are in `.claude/repo-index.json`
under `services["telemetry-comparison"].js_modules`.

| Module                         | Lines | Owns                                                                                                  |
|--------------------------------|------:|-------------------------------------------------------------------------------------------------------|
| `static/app.js`                |   177 | Bootstrap IIFE, panel-toggle glue, cross-module wiring on load.                                       |
| `static/modules/state.js`      |   184 | Shared `appState` / `videoState`, constants, `window.*` interop with classic `track-map.js`.           |
| `static/modules/data.js`       |   156 | `fetch*` calls to `/api/*`, interpolation + binary-search helpers.                                    |
| `static/modules/selections.js` |   282 | Row + dropdown + lap-picker UI, channel chips, cascading partition filters.                           |
| `static/modules/charts.js`     |   293 | Plotly lifecycle, marker drag (Pointer Events — mouse+touch+stylus), linked x-axes, corner overlay.   |
| `static/modules/sync.js`       |   437 | **Marker ↔ video bidirectional sync.** Sole owner of the `source === 'drag' \| 'video'` guard. rAF-only driver for display-rate smoothing of low-fps sidecars. |
| `static/modules/video.js`      |   249 | Video lap loading + picker + speed UI. No sync logic.                                                 |
| `static/modules/video-overlay.js` | 1114| Combined Video+Map dock/float overlay controller — drag, pinch/mouse resize, pointer capture. **Over the 500-line soft ceiling**; splitting deferred until another cleanup pass. |
| `static/modules/track-map.js`  |   483 | 2D track outline + corner badges + position dot. Classic script. Two ResizeObservers (tier legend hide + zoom re-fit, both float-aware). |
| `static/modules/toast.js`      |    63 | Top-center toast stack. Classic script → `window.showToast`.                                          |

Key contract: `sync.js` is the only file that inspects `source === 'drag' | 'video'`.
Charts import from `sync.js`; video imports from `sync.js`; they never import
from each other. See `.claude/skills/video-seeking/SKILL.md`.

---

## Agent triggers (when to ask which agent)

Agents live at `~/.claude/agents/devTeam-*.md`. Invoke via `/agent` or ask me
to pick one. Specs land in `dev-planning/<feature>/spec.md`.

| Agent                 | When to call                                                                                         | Spec/output location                                             |
|-----------------------|------------------------------------------------------------------------------------------------------|------------------------------------------------------------------|
| `Buddy`               | Feature idea → must produce a written spec before any code.                                          | writes `dev-planning/<feature>/spec.md`                          |
| `ArchDev`             | Implement spec / fix bugs. **Sole owner of production code.**                                         | writes code + `docs/architecture-<feature>.md`                   |
| `Tester`              | Write pytest / integration tests for shipped code. Skipped by default (Ludvík tests in Quix Cloud).  | writes tests under the owning service's `tests/`                 |
| `FrontEndEsthetic`    | CSS / visual polish on functional-but-unstyled markup. Uses Tailwind / component libs.               | edits `.css`, component files                                    |
| `NitpickerCustomer`   | UX audit of a feature once it's functionally complete. Token-heavy — skip unless scope unclear.      | writes `dev-planning/<project>/complaints/<feature>.md`          |
| `DocuGuy`             | User-facing docs once architecture stabilises.                                                       | writes `docs/*.md` (user-facing)                                 |
| `Presenter`           | Slide deck / demo script from a shipped feature.                                                     | writes under `docs/` or `dev-planning/`                          |

Frozen roster spec: `dev-planning/_devteam/team-buildout.md`.

---

## Common queries — where to look

- **"Where is symbol `X` defined?"** → `.claude/repo-index.json`,
  `services.<svc>.python_symbols[]` or `.js_modules[<file>].exports`.
- **"What routes does service `X` expose?"** → `services.<svc>.routes[]` in the
  JSON. Each entry has `method`, `path`, `file`, `line`, `handler`.
- **"Which module imports `X` in Telemetry Explorer?"** → grep
  `services.telemetry-comparison.js_modules.*.imports` in the JSON.
- **"Which `window.*` globals does the explorer publish?"** → each JS module's
  `window_globals` array in the JSON.
- **"How does a session flow from AC to Test Manager?"** → `CLAUDE.md`
  "Data flow" section (ASCII diagram).
- **"How does the marker/video sync work?"** → `.claude/skills/video-seeking/SKILL.md`
  and `sync.js` (indexed above).
- **"Which test touches X?"** → `test-manager-backend/tests/` is the only
  formal suite; not indexed by the JSON (`tests/` is skipped on purpose).

---

## Regenerating the index

```bash
python scripts/gen_repo_index.py          # rewrite .claude/repo-index.json
python scripts/gen_repo_index.py --check  # 0 fresh, 1 stale, 2 missing
```

- stdlib only; runs in under a second.
- Writes `newest_source_mtime` into the JSON; `--check` compares against the
  current tree and points at the most recently modified source file.
- Alphabetically sorted keys → minimal git diffs.
- Scope (v1): Python defs/classes + FastAPI routes in all top-level Python
  services; JS exports + `window.*` + imports for
  `telemetry-comparison/static/app.js` and `static/modules/*.js` only. The
  Next.js `test-manager-frontend/` is deliberately not scanned yet.

Architecture of this artifact: `docs/architecture-repo-orientation.md`.
