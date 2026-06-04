# Repo Orientation Artifact — Architecture

## What this is

A hybrid orientation layer that lets Claude (and humans) answer "where does X
live?" questions without repo-wide Glob/Grep cycles. Three artifacts produced
as a set:

1. **`REPO_INDEX.md`** (repo root, ~140 lines) — hand-written top-level map:
   service table, JS module map, agent triggers, common-query crib sheet.
2. **`.claude/repo-index.json`** (generated, ~53 KB) — machine-readable
   symbol/route/import index, keyed alphabetically for minimal diffs.
3. **`scripts/gen_repo_index.py`** (stdlib-only Python) — regenerator. Also
   supports `--check` mode for staleness detection.

A short "Orientation" section in `CLAUDE.md` points at both files.

## Why this shape

Spec `dev-planning/repo-orientation/spec.md` picked **hybrid** after rejecting
four alternatives:

- *`REPO_INDEX.md`-only:* rots at symbol granularity.
- *JSON-only:* loses the "why does this dir exist / which agent owns it"
  human-curated layer.
- *Per-service `MODULE.md`:* N surfaces to maintain instead of one.
- *Extend `CLAUDE.md`:* bloats the always-loaded system prompt.

The MD shell holds stable semantics (directory → purpose → owner). The JSON
holds churn-prone detail (symbol names, line numbers, route paths). Split
between them keeps the MD small enough to eyeball and the JSON automated
enough to stay true.

**No runtime dep added.** Spec's hard constraint. Generator uses `ast`, `json`,
`re`, `pathlib`, `datetime`, `sys`, `argparse` only.

## Key design choices

### Scope intentionally narrow in v1

- Python scan covers all nine top-level Python services listed in
  `PYTHON_SERVICES` in the script. Skip sets (`tests/`, `mock/`,
  `__pycache__/`, `.venv/`, `node_modules/`, build dirs, `recordings/`) prune
  obvious noise.
- JS scan covers **only** `telemetry-comparison/static/app.js` and
  `telemetry-comparison/static/modules/*.js`. The Next.js TS frontend is
  deliberately out — stdlib can't parse TS cleanly and "the files Claude
  actually asks about" all live in the Explorer (spec §8).
- Symbols extracted: module-top-level `def` / `async def` / `class` only.
  Nested helpers and methods are not indexed — this is an orientation layer,
  not an LSP.

### Route detection via AST, not regex

For FastAPI routes the generator walks each function's `decorator_list` and
matches any `@<receiver>.<http_method>(...)` call. Critically, **it does not
hardcode `app` or `router`** as the receiver name — some services alias the
app as `api` (`ac_video_streaming/viewer.py`) or use multiple routers. The
HTTP-method allowlist is the gate: `get`, `post`, `put`, `delete`, `patch`,
`head`, `options`.

This correctly handles multi-line decorators like

```python
@router.get(
    "/tests", response_model=PaginatedResponse[Test], ...
)
def list_tests(...):
```

which a line-anchored regex would miss.

### JS handled by regex, not AST

Spec explicitly authorises regex for JS (stdlib has no JS parser and the
constraints forbid new deps). Five patterns cover v1:

- `^export (async )?function NAME` → named function export.
- `^export (const|let|var) NAME` → named binding export.
- `^export class NAME` → class export.
- `^export { a, b as c }` → named re-export block (multi-name).
- `^window.NAME =` (anywhere, leading whitespace allowed) → window global.

Imports are parsed twice:

1. A multi-line regex (`re.DOTALL`) catches bracketed named imports that span
   multiple lines. Deduplication is keyed on `(origin, tuple(names))`.
2. A per-line pass catches default imports and side-effect imports that the
   multi-line regex doesn't cover.

This is imperfect (e.g. dynamic `import()` is ignored, function-expression
exports aren't caught), but the miss-cost is "a nuisance, not a correctness
bug" (spec §8).

### Stable diffs

Every list and dict in the output is sorted:

- Services dict alphabetical.
- Python symbols sorted by `(file, line, name)`.
- Routes sorted by `(file, line, method, path)`.
- JS exports alphabetical and de-duplicated.
- JS imports sorted by `(from, names)`.
- `json.dumps(..., indent=2, sort_keys=True)` at the top level.

A regeneration after no source change produces a byte-identical file.

### Staleness strategy

Per the spec's clarified wording, the script **always overwrites** the JSON on
a bare invocation. Freshness is a read-side concern:

- On write, the generator embeds `newest_source_mtime` (ISO-8601, UTC, second
  precision) — the newest mtime across every scanned file.
- `--check` mode re-walks the tree, recomputes the newest mtime, and compares
  against the stored value. Exit codes: 0 fresh, 1 stale (prints the offending
  file path), 2 missing/unreadable.

This is **not** a commit gate. Ludvík rejected hooks and CI — friction he
doesn't want. The `--check` flag is intended for optional manual sanity or
future opt-in automation.

## Data flows

### Write path

```
gen_repo_index.py (bare)
  ├─ build_index()
  │    ├─ for each service in PYTHON_SERVICES:
  │    │    iter_python_files()  → ast.parse → symbols + routes
  │    ├─ for each service in JS_SCANS:
  │    │    iter_js_files()      → regex line-scan → exports, imports, window
  │    └─ compute_newest_source_mtime() → newest_source_mtime field
  └─ write_index()
       json.dumps(..., sort_keys=True, indent=2) → .claude/repo-index.json
```

### Check path

```
gen_repo_index.py --check
  ├─ read .claude/repo-index.json → stored newest_source_mtime
  ├─ compute_newest_source_mtime() → current
  └─ compare:
       equal    → exit 0
       differ   → print stale message with path, exit 1
       missing  → exit 2
```

### Consumer path (Claude / human)

```
"Where is updateMarker defined?"
  └─ Read .claude/repo-index.json
       └─ services["telemetry-comparison"]
            .js_modules["static/modules/sync.js"]
              .exports contains "updateMarker"
                └─ Read sync.js starting near line 63 (known from a prior Grep)
```

The JSON doesn't store line numbers for JS exports (v1 scope) — the file
location is enough to target a single focused Read.

## File inventory

| Path                                                  | New / Modified | Why                                                             |
|-------------------------------------------------------|----------------|-----------------------------------------------------------------|
| `REPO_INDEX.md`                                       | New            | Hand-written top-level map, module table, agent triggers.       |
| `.claude/repo-index.json`                             | New (generated)| Machine symbol/route/import index. ~53 KB, nine services.        |
| `scripts/gen_repo_index.py`                           | New            | stdlib-only regenerator. Supports `--check`.                    |
| `CLAUDE.md`                                           | Modified       | Appended a short "Orientation" section pointing at the two.     |
| `docs/architecture-repo-orientation.md`               | New            | This doc.                                                        |

`scripts/` previously did not exist at the repo root — the generator creates
it via its own presence.

## Integration with existing orientation stack

The orientation stack in this repo is now:

```
CLAUDE.md              prose architecture + conventions (always-loaded for Claude)
REPO_INDEX.md          top-level map, module map, agent triggers (on-demand Read)
.claude/repo-index.json symbol/route/import index                 (on-demand Read)
.claude/skills/*       task playbooks (video-seeking, video-capture, ...)
~/.claude/agents/*     agent roster (not committed, per-user)
user memory            session continuity (not committed, per-user)
```

`CLAUDE.md` keeps its narrative tone. `REPO_INDEX.md` is deliberately lookup-
only — tables and pointers, no tutorials. The JSON is the only churny layer
and the only one that needs regeneration.

## Caveats / follow-ups

- **`test-manager-frontend/` TS not indexed.** Deferred per spec §8. If Claude
  queries keep hitting that dir, add a `ts`-aware path using `tsc --noEmit
  --declaration` output or a tree-sitter build — but not before real demand.
- **`test_*.py` outside `tests/` dirs are still scanned.** E.g. `ac_video_streaming/test_stream.py` exposes a route and ends up indexed. That's
  correct: it's a runnable script, not a pytest module. If it becomes noise,
  add name-level filtering.
- **`ac-video-browser` / `ac-video-viewer`** are flagged `fastapi` because
  they expose routes, but they're disabled by default in `quix.yaml`. The JSON
  doesn't currently surface "disabled" state.
- **Size target.** Spec §8 aimed for <30 KB. We're at ~53 KB after Ludvík
  expanded scope to all Python services. If token cost bites, the first
  easy win is dropping `_private` symbols from the symbol list.

## Runbook

```bash
# Regenerate after adding/moving a top-level dir or renaming public symbols
python scripts/gen_repo_index.py

# Non-zero exit => index is stale; re-run the generator and commit
python scripts/gen_repo_index.py --check

# Syntax-only sanity check on the generator itself
python -m py_compile scripts/gen_repo_index.py
```
