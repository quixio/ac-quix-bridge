"""
Regenerate the machine-readable repo index at .claude/repo-index.json.

Purpose: give Claude (and other LLMs) a cheap, deterministic way to answer
"where is <symbol>?" / "what routes does <service> expose?" without repo-wide
globbing. Companion to the hand-written REPO_INDEX.md.

Usage:
    python scripts/gen_repo_index.py          # rewrite .claude/repo-index.json
    python scripts/gen_repo_index.py --check  # exit non-zero if index is stale

Design constraints:
  - stdlib only (ast, json, pathlib, re, datetime, sys, argparse).
  - No third-party deps => runs on any clean Python 3.10+ checkout.
  - Output is pretty-printed with sorted keys => git diffs stay minimal.
  - Relative paths, forward slashes => stable across OSes.
  - Line numbers are 1-indexed (matches editor + Read tool conventions).

v1 scope (see dev-planning/repo-orientation/spec.md):
  - Python: top-level def/async def/class + FastAPI route decorators.
  - JS: telemetry-comparison/static/modules/*.js + static/app.js only.
        Export, window.* assignments, import graph.
  - Skips: __pycache__, .venv, node_modules, tests/, mock/.

Not in scope: TS, Next.js frontend, nested symbols, docstring extraction,
AST-accurate JS parsing. Add incrementally if real queries demand it.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / ".claude" / "repo-index.json"

# Top-level service directories to scan. Kept explicit (not auto-discovered) so
# a new experimental dir doesn't silently bloat the index until it's promoted.
PYTHON_SERVICES: dict[str, str] = {
    "ac-telemetry-source": "quixstreams-source",
    "ac-telemetry-lake": "quixstreams-sink",
    "telemetry-dashboard": "fastapi",
    "telemetry-comparison": "fastapi",
    "session-config-bridge": "quixstreams-app",
    "test-manager-backend": "fastapi",
    "ac_video_streaming": "quixstreams-source",
    "ac-video-viewer": "fastapi",
    "ac-video-browser": "fastapi",
}

# JS scan is narrow in v1 — only the Telemetry Explorer frontend. The Next.js
# test-manager-frontend is deliberately omitted (TS needs a real parser).
JS_SCANS: dict[str, list[str]] = {
    "telemetry-comparison": [
        "static/app.js",
        "static/modules/*.js",
    ],
}

# Directory names that must never be descended into regardless of service.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "tests",
        "test",
        "mock",
        "mocks",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "recordings",
    }
)

HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options"}
)

# JS regexes. Deliberately line-anchored — function expressions assigned inside
# other expressions aren't public module API and don't need to show up here.
RE_JS_EXPORT_FUNC = re.compile(
    r"^export\s+(?:async\s+)?function\s+(\w+)",
)
RE_JS_EXPORT_BINDING = re.compile(
    r"^export\s+(?:const|let|var)\s+(\w+)",
)
RE_JS_EXPORT_CLASS = re.compile(r"^export\s+class\s+(\w+)")
RE_JS_EXPORT_NAMED = re.compile(r"^export\s*\{([^}]+)\}")
RE_JS_WINDOW_ASSIGN = re.compile(r"^\s*window\.(\w+)\s*=")
RE_JS_IMPORT_NAMED = re.compile(
    r"""^import\s+(?:(\w+)\s*,\s*)?\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]""",
)
RE_JS_IMPORT_DEFAULT = re.compile(
    r"""^import\s+(\w+)\s+from\s+['"]([^'"]+)['"]""",
)
RE_JS_IMPORT_SIDE_EFFECT = re.compile(r"""^import\s+['"]([^'"]+)['"]""")
# Multi-line `import { ... } from '...'` — joined before matching.
RE_JS_IMPORT_MULTILINE = re.compile(
    r"""import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]""",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def rel(path: Path, base: Path) -> str:
    """Return ``path`` relative to ``base`` using forward slashes."""
    return path.relative_to(base).as_posix()


def iter_python_files(service_dir: Path) -> list[Path]:
    """Yield .py files under ``service_dir``, pruning SKIP_DIRS on the walk."""
    results: list[Path] = []
    for path in service_dir.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(service_dir).parts):
            continue
        results.append(path)
    return results


def iter_js_files(service_dir: Path, globs: list[str]) -> list[Path]:
    results: list[Path] = []
    for pattern in globs:
        for path in sorted(service_dir.glob(pattern)):
            if path.is_file() and path.suffix in {".js", ".mjs"}:
                # Still honour SKIP_DIRS even under the whitelist (defensive).
                if any(
                    part in SKIP_DIRS
                    for part in path.relative_to(service_dir).parts
                ):
                    continue
                results.append(path)
    return results


# ---------------------------------------------------------------------------
# Python scanning
# ---------------------------------------------------------------------------


def _decorator_method_and_path(dec: ast.expr) -> tuple[str, str] | None:
    """If ``dec`` is ``@something.<http_method>("/path", ...)``, return
    ``(METHOD, path)``. Otherwise return None.

    We accept ANY receiver (``app``, ``router``, ``v1``, ...) as long as the
    attribute matches an HTTP method — avoids hardcoding the Python variable
    name the service happens to use (spec §5: don't hardcode `app`/`router`).
    """
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return None
    method = func.attr.lower()
    if method not in HTTP_METHODS:
        return None
    if not dec.args:
        return None
    first = dec.args[0]
    # ast.Constant covers str literals in Python 3.8+; ast.Str is the legacy
    # alias and still appears on some transformers — accept both.
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return method.upper(), first.value
    if isinstance(first, ast.Str):  # pragma: no cover — py<3.12 legacy
        return method.upper(), first.s
    return None


def scan_python_file(path: Path, service_dir: Path) -> tuple[list[dict], list[dict]]:
    """Return ``(symbols, routes)`` for one Python file.

    ``symbols`` are top-level function/class defs (module scope only). Nested
    helpers and methods are out of scope — the index is for orientation, not
    symbol resolution.
    """
    symbols: list[dict] = []
    routes: list[dict] = []
    rel_path = rel(path, service_dir)

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"warn: could not read {path}: {e}", file=sys.stderr)
        return symbols, routes

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"warn: syntax error in {path}: {e}", file=sys.stderr)
        return symbols, routes

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            symbols.append(
                {
                    "name": node.name,
                    "kind": kind,
                    "file": rel_path,
                    "line": node.lineno,
                }
            )
            for dec in node.decorator_list:
                parsed = _decorator_method_and_path(dec)
                if parsed is not None:
                    method, route_path = parsed
                    routes.append(
                        {
                            "method": method,
                            "path": route_path,
                            "file": rel_path,
                            "line": dec.lineno,
                            "handler": node.name,
                        }
                    )
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                {
                    "name": node.name,
                    "kind": "class",
                    "file": rel_path,
                    "line": node.lineno,
                }
            )

    return symbols, routes


def scan_python_service(service_dir: Path) -> tuple[list[dict], list[dict]]:
    all_symbols: list[dict] = []
    all_routes: list[dict] = []
    for py in iter_python_files(service_dir):
        symbols, routes = scan_python_file(py, service_dir)
        all_symbols.extend(symbols)
        all_routes.extend(routes)
    # Stable order: (file, line) for deterministic diffs.
    all_symbols.sort(key=lambda s: (s["file"], s["line"], s["name"]))
    all_routes.sort(key=lambda r: (r["file"], r["line"], r["method"], r["path"]))
    return all_symbols, all_routes


# ---------------------------------------------------------------------------
# JS scanning
# ---------------------------------------------------------------------------


def _split_import_names(group: str) -> list[str]:
    """``{foo, bar as baz, qux}`` → ``['foo', 'baz', 'qux']`` (import local
    names only). We keep the local alias because that's what callers see."""
    names: list[str] = []
    for piece in group.split(","):
        piece = piece.strip()
        if not piece:
            continue
        # `foo as bar` → take `bar`; plain `foo` → take `foo`.
        parts = [p.strip() for p in piece.split(" as ")]
        names.append(parts[-1])
    return names


def scan_js_file(path: Path, service_dir: Path) -> dict:
    """Return the js_modules entry for ``path``."""
    rel_path = rel(path, service_dir)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"warn: could not read {path}: {e}", file=sys.stderr)
        return {"exports": [], "imports": [], "window_globals": []}

    exports: list[str] = []
    window_globals: list[str] = []

    for line in source.splitlines():
        m = RE_JS_EXPORT_FUNC.match(line)
        if m:
            exports.append(m.group(1))
            continue
        m = RE_JS_EXPORT_BINDING.match(line)
        if m:
            exports.append(m.group(1))
            continue
        m = RE_JS_EXPORT_CLASS.match(line)
        if m:
            exports.append(m.group(1))
            continue
        m = RE_JS_EXPORT_NAMED.match(line)
        if m:
            # `export { a, b as c };` — report the exported (outer) name.
            for piece in m.group(1).split(","):
                piece = piece.strip()
                if not piece:
                    continue
                parts = [p.strip() for p in piece.split(" as ")]
                # For `a as b`, the exported name is `b` (what importers see).
                exports.append(parts[-1])
            continue
        m = RE_JS_WINDOW_ASSIGN.match(line)
        if m:
            window_globals.append(m.group(1))

    # Imports — try multi-line first, fall back to single-line patterns.
    # RE_JS_IMPORT_MULTILINE handles `import {\n  a,\n  b,\n} from './x.js'`.
    imports: list[dict] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for m in RE_JS_IMPORT_MULTILINE.finditer(source):
        names = tuple(_split_import_names(m.group(1)))
        origin = m.group(2)
        key = (origin, names)
        if key in seen:
            continue
        seen.add(key)
        imports.append({"from": origin, "names": list(names)})

    # Default-only and side-effect imports aren't caught by the multiline
    # named-import regex. Walk the lines for those separately.
    for line in source.splitlines():
        md = RE_JS_IMPORT_DEFAULT.match(line)
        if md and "{" not in line:
            origin = md.group(2)
            name = md.group(1)
            key = (origin, (name,))
            if key not in seen:
                seen.add(key)
                imports.append({"from": origin, "names": [name]})
            continue
        ms = RE_JS_IMPORT_SIDE_EFFECT.match(line)
        if ms and " from " not in line:
            origin = ms.group(1)
            key = (origin, ())
            if key not in seen:
                seen.add(key)
                imports.append({"from": origin, "names": []})

    # De-dup exports while preserving first-seen order for readability.
    dedup_exports: list[str] = []
    seen_exp: set[str] = set()
    for name in exports:
        if name not in seen_exp:
            seen_exp.add(name)
            dedup_exports.append(name)

    dedup_exports.sort()
    window_globals = sorted(set(window_globals))
    imports.sort(key=lambda i: (i["from"], tuple(i["names"])))

    return {
        "exports": dedup_exports,
        "imports": imports,
        "window_globals": window_globals,
    }


def scan_js_service(service_dir: Path, globs: list[str]) -> dict:
    modules: dict = {}
    for js in iter_js_files(service_dir, globs):
        rel_path = rel(js, service_dir)
        modules[rel_path] = scan_js_file(js, service_dir)
    return dict(sorted(modules.items()))


# ---------------------------------------------------------------------------
# Freshness helpers
# ---------------------------------------------------------------------------


def find_entry_point(service_dir: Path) -> str | None:
    """Prefer ``main.py`` if present (repo convention); otherwise None."""
    main_py = service_dir / "main.py"
    if main_py.exists():
        return "main.py"
    return None


def compute_newest_source_mtime() -> tuple[datetime, Path | None]:
    """Return the newest mtime across all files the generator scans.

    Used both in build mode (embedded in the JSON) and in --check mode (compared
    against the JSON's own mtime).
    """
    newest: float = 0.0
    newest_path: Path | None = None
    for service, _ in PYTHON_SERVICES.items():
        service_dir = REPO_ROOT / service
        if not service_dir.exists():
            continue
        for py in iter_python_files(service_dir):
            m = py.stat().st_mtime
            if m > newest:
                newest = m
                newest_path = py
    for service, globs in JS_SCANS.items():
        service_dir = REPO_ROOT / service
        if not service_dir.exists():
            continue
        for js in iter_js_files(service_dir, globs):
            m = js.stat().st_mtime
            if m > newest:
                newest = m
                newest_path = js
    dt = datetime.fromtimestamp(newest, tz=timezone.utc) if newest else datetime.now(timezone.utc)
    return dt, newest_path


def format_iso(dt: datetime) -> str:
    """Format to second precision, UTC, without microseconds — stable diffs."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


# ---------------------------------------------------------------------------
# Build / check modes
# ---------------------------------------------------------------------------


def build_index() -> dict:
    services: dict = {}
    for service, kind in PYTHON_SERVICES.items():
        service_dir = REPO_ROOT / service
        if not service_dir.exists():
            print(f"warn: declared service missing: {service}", file=sys.stderr)
            continue
        symbols, routes = scan_python_service(service_dir)
        entry = {
            "kind": kind,
            "entry": find_entry_point(service_dir),
            "python_symbols": symbols,
            "routes": routes,
        }
        js_globs = JS_SCANS.get(service)
        if js_globs:
            entry["js_modules"] = scan_js_service(service_dir, js_globs)
        services[service] = entry

    newest_dt, _ = compute_newest_source_mtime()
    return {
        "generated_at": format_iso(datetime.now(timezone.utc)),
        "newest_source_mtime": format_iso(newest_dt),
        "services": dict(sorted(services.items())),
    }


def write_index(index: dict) -> int:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys=True keeps service sub-dicts (kind, entry, ...) alphabetical.
    # The top-level ordering (generated_at / newest_source_mtime / services)
    # is also alphabetical under sort_keys — matches the stable-diff goal.
    payload = json.dumps(index, indent=2, sort_keys=True) + "\n"
    INDEX_PATH.write_text(payload, encoding="utf-8")
    return len(payload.encode("utf-8"))


def check_stale() -> int:
    """Return 0 if index is fresh, 1 if stale, 2 if index missing or unreadable."""
    if not INDEX_PATH.exists():
        print(
            f"stale: {INDEX_PATH.relative_to(REPO_ROOT)} does not exist — run "
            "`python scripts/gen_repo_index.py`",
            file=sys.stderr,
        )
        return 2
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"stale: cannot read index: {e}", file=sys.stderr)
        return 2

    stored = data.get("newest_source_mtime")
    current_dt, current_path = compute_newest_source_mtime()
    current = format_iso(current_dt)
    if stored != current:
        where = (
            current_path.relative_to(REPO_ROOT).as_posix()
            if current_path is not None
            else "<unknown>"
        )
        print(
            f"stale: newest source mtime is {current} ({where}); "
            f"index recorded {stored}. "
            "Run `python scripts/gen_repo_index.py` and commit.",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Don't write the index — compare it against the current tree. "
            "Exit 0 if fresh, 1 if stale, 2 if missing/unreadable."
        ),
    )
    args = parser.parse_args(argv)

    if args.check:
        return check_stale()

    index = build_index()
    size = write_index(index)
    print(
        f"wrote {INDEX_PATH.relative_to(REPO_ROOT)} ({size} bytes, "
        f"{len(index['services'])} services)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
