"""Entry point. Run with `uv run python main.py`."""

import uvicorn


def run() -> None:
    uvicorn.run("app:app", host="127.0.0.1", port=8770, reload=True)


if __name__ == "__main__":
    run()
