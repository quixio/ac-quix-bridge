# Mock QuixLake API (local dev)

Serves saved per-session telemetry CSVs so the **post-race telemetry viz** (PDF +
AI Summary card) can be developed/verified locally without real lake credentials.

`shared/post_race_ai/lake.py` POSTs SQL to `Quix__Lakehouse__Query__Url`; in the
dev stack that points at this service (`docker-compose.dev.yml`), which pulls the
`session_id = '...'` literal out of the WHERE clause and returns the matching
fixture from `data/` (header-only CSV for unknown sessions → the viz omits its
section, same as production). Stdlib only, no deps.

## Run

Comes up with the dev stack:

```bash
docker compose -f docker-compose.dev.yml up -d
```

The backend is wired to it via `Quix__Lakehouse__Query__Url=http://mock-lake:8002`.

## Fixtures (gitignored — regenerate locally)

`data/*.csv` and `test-manager-backend/scripts/seed_data/` are **not committed**
(real session data). Regenerate them:

1. **Per-lap CSVs** — for each session you want, run the exact query
   `shared/post_race_ai/telemetry_viz.build_session_sql` produces against the
   real lake (e.g. via the quixlake MCP) and save the CSV to
   `data/<session_id with every non [A-Za-z0-9.] char replaced by _>.csv`
   (must match `mock_lake_api/main.fixture_name`). Header:
   `lap,pos,speedKmh,gas,brake,gear,iCurrentTime,isValidLap,timestamp_ms`.

2. **Analyses + tests** — fetch completed analyses + their tests from a Test
   Manager backend and drop the JSON into
   `test-manager-backend/scripts/seed_data/` (`analyses_complete.json` from
   `GET /api/v1/analyses?status=complete`, `test_<id>.json` from
   `GET /api/v1/tests/{id}`).

3. **Seed Mongo**:
   ```bash
   docker compose -f docker-compose.dev.yml exec backend \
       uv run python scripts/seed_local_viz.py
   ```

Then open a completed session analysis in the card, or
`GET /api/v1/analyses/{id}/pdf`.
