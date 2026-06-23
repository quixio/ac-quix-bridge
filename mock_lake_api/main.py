"""Mock QuixLake query API for local development of the post-race telemetry viz.

Mimics the lake `/query` endpoint just enough for `shared/post_race_ai/lake.py`:
accepts a POST of raw SQL, pulls the `session_id = '...'` literal out of the
WHERE clause, and returns the pre-saved CSV fixture for that session (exactly
the bytes the real lake returned for the same query, captured via the
quixlake MCP). Unknown sessions get a header-only CSV so the cleaning pipeline
sees "no data" and the report omits the telemetry section — same as production.

No third-party deps (stdlib only) so the container needs no lockfile.
"""

import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mock-lake")

DATA_DIR = os.environ.get("FIXTURE_DIR", "/data")
PORT = int(os.environ.get("PORT", "8002"))

# The exact column header build_session_sql produces — returned when a session
# has no fixture, so pandas reads an empty frame (0 rows) rather than erroring.
_EMPTY_CSV = (
    "lap,pos,speedKmh,gas,brake,gear,iCurrentTime,isValidLap,timestamp_ms\n"
)
_SESSION_RE = re.compile(r"session_id\s*=\s*'([^']+)'", re.IGNORECASE)


def fixture_name(session_id: str) -> str:
    """Filesystem-safe fixture filename for a session id (must match the puller)."""
    return re.sub(r"[^A-Za-z0-9.]", "_", session_id) + ".csv"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence default stderr spam
        return

    def _send(self, code: int, body: str, content_type: str = "text/csv") -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, "ok", "text/plain")
            return
        self._send(404, "not found", "text/plain")

    def do_POST(self) -> None:
        if not self.path.startswith("/query"):
            self._send(404, "not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0"))
        sql = self.rfile.read(length).decode("utf-8") if length else ""
        match = _SESSION_RE.search(sql)
        if not match:
            logger.warning("[mock-lake] no session_id in SQL -> empty result")
            self._send(200, _EMPTY_CSV)
            return
        session_id = match.group(1)
        path = os.path.join(DATA_DIR, fixture_name(session_id))
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            rows = body.count("\n") - 1
            logger.info("[mock-lake] session %s -> %d rows", session_id, max(rows, 0))
            self._send(200, body)
        else:
            logger.info("[mock-lake] session %s -> no fixture (empty)", session_id)
            self._send(200, _EMPTY_CSV)


def main() -> None:
    logger.info("[mock-lake] serving fixtures from %s on :%d", DATA_DIR, PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
