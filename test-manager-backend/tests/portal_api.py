import sys
from typing import Any

import uvicorn
from fastapi import FastAPI

app = FastAPI(title="Mock Portal API")


@app.get("/auth/permissions/query")
def validate_permissions() -> dict[str, Any]:
    return {"allowed": True, "user_id": "mock-user", "workspace_id": "workspace-id"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(sys.argv[1]))
