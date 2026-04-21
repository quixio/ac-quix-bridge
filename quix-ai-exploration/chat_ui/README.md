# Chat UI probe

Minimal FastAPI + vanilla HTML/JS chat that proxies Quix Portal AI
workspace chat and streams SSE responses to the browser. Token stays
server-side.

## Run

```bash
cd quix-ai-exploration/chat_ui
uv run python main.py
```

Open http://localhost:8770.

Reads `../probes/.env` for `QUIX_PORTAL_API`, `QUIX_TOKEN`,
`QUIX_WORKSPACE_ID`, `QUIX_WORKSPACE_NAME`.

## Layout

```
chat_ui/
├── main.py                  # uvicorn entry point
├── pyproject.toml           # uv-managed deps
├── app/
│   ├── __init__.py          # create_app() + StaticFiles mount
│   ├── config.py            # env + portal_headers() + portal_context()
│   ├── quix.py              # async QuixAI client (session create, SSE stream)
│   └── routes.py            # POST /api/chat
└── static/
    ├── index.html           # HTML skeleton only
    ├── style.css
    ├── sse.js               # reusable SSE stream parser
    └── chat.js              # chat UI logic
```

## Gates

Backend (Python):

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pytest                 # fast tests only (default)
uv run pytest --run-slow      # include integration (hits real Portal)
```

Frontend (static HTML/CSS/JS):

```bash
npm install    # one-time
npm run check  # eslint + prettier --check
npm run format # prettier --write
```

### Test markers

- **unmarked** — fast, no network, no containers. Runs on every `uv run pytest`.
- **`@pytest.mark.slow`** — anything slow (network, containers, long fixtures). Skipped by default.
- **`@pytest.mark.integration`** — subset of `slow` that hits the real Quix Portal API. Skipped by default.

Opt in with `--run-slow` (flag defined in `tests/conftest.py`). Filter to a subset with `-m slow` or `-m integration`.

## What it does

- First message → `POST /ai/api/sessions` to mint a session, then
  `POST /ai/api/sessions/{id}/messages` with the prompt.
- Subsequent messages reuse the same `session_id` (kept in the browser).
- SSE stream from QuixAI is forwarded verbatim to the browser plus a
  synthetic `event: session` first frame carrying the session id.
- Client parses `text_delta`, `session_title`, `context_warning`; ignores
  `status`, `usage`, `[DONE]`.
