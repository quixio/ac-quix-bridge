#!/usr/bin/env bash
# Run the test-manager-backend pytest suite on macOS without papercuts.
#
# Two host-only gotchas this handles:
#   1. WeasyPrint (PDF export tests) needs Pango/gobject — point the dynamic
#      loader at brew's libs. Without this the PDF tests error with
#      "cannot load library 'libgobject-2.0-0'". `brew install pango` first.
#   2. The dev backend container bind-mounts ./test-manager-backend/.venv, so a
#      bare `uv run` on the host rebuilds that venv with macOS binaries and
#      kills the container. An isolated UV_PROJECT_ENVIRONMENT avoids that.
#
# testcontainers still needs Docker Desktop running (spawns an ephemeral Mongo).
# Args are forwarded to pytest:  scripts/test-backend.sh -k last_requirements -v
set -euo pipefail

cd "$(dirname "$0")/../test-manager-backend"

exec env \
  DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib" \
  UV_PROJECT_ENVIRONMENT=/tmp/tm-test-venv \
  uv run pytest "$@"
