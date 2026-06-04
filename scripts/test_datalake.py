"""Test pull from the Quix Data Lake query API.

Uses the same credential resolution and SQL as the dashboard's /leaderboard
endpoint, so a green run here means the deployed leaderboard will work too.
Reads creds from the environment (the .ps1 wrapper loads a .env first).
"""
import os
import sys

import httpx


def env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


url = env("DATALAKE_API_URL", "Quix__Lakehouse__Query__Url")
token = env("DATALAKE_API_TOKEN", "Quix__Lakehouse__Query__AuthToken")

if not url or not token:
    print("MISSING CREDENTIALS.")
    print("Set one of these pairs (e.g. in telemetry-dashboard/.env), then re-run:")
    print("  DATALAKE_API_URL / DATALAKE_API_TOKEN")
    print("  Quix__Lakehouse__Query__Url / Quix__Lakehouse__Query__AuthToken")
    sys.exit(2)

url = url.rstrip("/")
SQL = os.environ.get("LEADERBOARD_SQL") or (
    'SELECT driver AS name, MIN("iBestTime") AS ms '
    'FROM ac_telemetry '
    "WHERE \"iBestTime\" > 0 AND driver IS NOT NULL AND driver <> '' "
    "GROUP BY driver ORDER BY ms ASC LIMIT 10"
)
headers = {"Authorization": f"Bearer {token}"}
print(f"Query API: {url}\n")


def show(label, resp):
    print(f"--- {label} -> HTTP {resp.status_code} ---")
    print(resp.text[:1200].rstrip())
    print()


with httpx.Client(timeout=30) as c:
    try:
        show("GET /tables", c.get(f"{url}/tables", headers=headers))
    except Exception as e:
        print(f"GET /tables FAILED: {e}\n")

    try:
        show("GET /schema?table=ac_telemetry",
             c.get(f"{url}/schema", params={"table": "ac_telemetry"}, headers=headers))
    except Exception as e:
        print(f"GET /schema FAILED: {e}\n")

    print(f"--- SQL ---\n{SQL}\n")
    try:
        r = c.post(f"{url}/query", content=SQL.encode("utf-8"),
                   headers={**headers, "Content-Type": "text/plain"})
        show("POST /query (leaderboard)", r)
        if r.status_code == 200 and "# ERROR" not in r.text:
            n = max(0, len(r.text.strip().splitlines()) - 1)
            print(f"RESULT: {n} driver row(s) returned." if n else "RESULT: 0 rows — query ran but the lake has no matching data yet.")
    except Exception as e:
        print(f"POST /query FAILED: {e}")
        sys.exit(1)
