"""
Mock Dynamic Configuration Manager API.

In-memory implementation of the Quix Configuration API for local development
and testing. Built from the DCM OpenAPI spec.

Storage model:
  configs[config_id] = {
      "type": str,
      "target_key": str,
      "category": str,
      "versions": {
          1: {"content": dict, "valid_from": str|None, "created_at": str, "sha256sum": None, ...},
          2: { ... },
      }
  }

Each version is independently addressable and deletable.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="Mock Configuration API",
    version="0.1.0",
    description="Mock of Quix Dynamic Configuration Manager for local dev/testing",
)

# ---------------------------------------------------------------------------
# In-memory storage
# ---------------------------------------------------------------------------

configs: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: dict | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _max_version(cfg: dict) -> int:
    return max(cfg["versions"].keys()) if cfg["versions"] else 0


def _version_metadata(config_id: str, cfg: dict, version: int) -> dict:
    v = cfg["versions"][version]
    return {
        "id": config_id,
        "metadata": {
            "type": cfg["type"],
            "target_key": cfg["target_key"],
            "category": cfg.get("category", ""),
            "version": version,
            "valid_from": v.get("valid_from"),
            "created_at": v["created_at"],
            "sha256sum": v.get("sha256sum"),
            "content_type": "json",
            "content_filename": None,
        },
    }


def _find_by_type_and_target(type_: str, target_key: str) -> str | None:
    for cid, cfg in configs.items():
        if cfg["type"] == type_ and cfg["target_key"] == target_key:
            return cid
    return None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok", "service": "mock-config-api"}


# ---------------------------------------------------------------------------
# POST /api/v1/configurations — create or replace
# ---------------------------------------------------------------------------


class MetadataInsert(BaseModel):
    type: str
    target_key: str
    category: str = ""
    valid_from: str | None = None


class ConfigInsert(BaseModel):
    metadata: MetadataInsert
    content: dict[str, Any] | None = None
    replace: bool | None = False


@app.post("/api/v1/configurations", status_code=201)
def create_configuration(body: ConfigInsert = Body(...)):
    now = _now_iso()
    sha = _content_hash(body.content)

    existing_id = None
    if body.replace:
        existing_id = _find_by_type_and_target(
            body.metadata.type, body.metadata.target_key
        )

    if existing_id:
        cfg = configs[existing_id]
        new_version = _max_version(cfg) + 1
        cfg["versions"][new_version] = {
            "content": body.content,
            "valid_from": body.metadata.valid_from,
            "created_at": now,
            "sha256sum": sha,
        }
        cfg["category"] = body.metadata.category
        return {"data": _version_metadata(existing_id, cfg, new_version), "links": {}}
    else:
        config_id = str(uuid.uuid4())
        configs[config_id] = {
            "type": body.metadata.type,
            "target_key": body.metadata.target_key,
            "category": body.metadata.category,
            "versions": {
                1: {
                    "content": body.content,
                    "valid_from": body.metadata.valid_from,
                    "created_at": now,
                    "sha256sum": sha,
                }
            },
        }
        return {
            "data": _version_metadata(config_id, configs[config_id], 1),
            "links": {},
        }


# ---------------------------------------------------------------------------
# GET /api/v1/configurations — search with filters
# ---------------------------------------------------------------------------


@app.get("/api/v1/configurations")
def search_configurations(
    type: str | None = None,
    target_key: str | None = None,
    category: str | None = None,
    id: str | None = None,
    version: int | None = None,
    sort: str = "created_at",
    sort_direction: str = "desc",
    limit: int = 0,
    offset: int = 0,
):
    results = []
    for config_id, cfg in configs.items():
        if type and cfg["type"] != type:
            continue
        if target_key and cfg["target_key"] != target_key:
            continue
        if category and cfg.get("category", "") != category:
            continue
        if id and config_id != id:
            continue

        if version is not None:
            if version in cfg["versions"]:
                results.append(_version_metadata(config_id, cfg, version))
        else:
            # Return latest version for each config
            max_v = _max_version(cfg)
            if max_v:
                results.append(_version_metadata(config_id, cfg, max_v))

    # Sort
    def sort_key(item: dict) -> Any:
        md = item["metadata"]
        return md.get(sort, md.get("created_at", ""))

    results.sort(key=sort_key, reverse=(sort_direction == "desc"))

    # Pagination
    total = len(results)
    if offset:
        results = results[offset:]
    if limit and limit > 0:
        results = results[:limit]

    return {"data": results, "links": {}, "count": total}


# ---------------------------------------------------------------------------
# GET /api/v1/configurations/{id} — get config (latest or specific version)
# ---------------------------------------------------------------------------


@app.get("/api/v1/configurations/{config_id}")
def get_configuration(config_id: str, version: int | None = None):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]
    v = version if version is not None else _max_version(cfg)

    if v is None or v not in cfg["versions"]:
        raise HTTPException(status_code=404, detail="Version not found")

    return {"data": _version_metadata(config_id, cfg, v), "links": {}}


# ---------------------------------------------------------------------------
# GET /api/v1/configurations/{id}/content — get content (latest or ?version=N)
# ---------------------------------------------------------------------------


@app.get("/api/v1/configurations/{config_id}/content")
def get_configuration_content(config_id: str, version: int | None = None):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]
    v = version if version is not None else _max_version(cfg)

    if v is None or v not in cfg["versions"]:
        raise HTTPException(status_code=404, detail="Version not found")

    return cfg["versions"][v].get("content") or {}


# ---------------------------------------------------------------------------
# GET /api/v1/configurations/{id}/versions — list all versions
# ---------------------------------------------------------------------------


@app.get("/api/v1/configurations/{config_id}/versions")
def get_configuration_versions(config_id: str):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]
    results = [
        _version_metadata(config_id, cfg, v) for v in sorted(cfg["versions"].keys())
    ]
    return {"data": results, "links": {}, "count": len(results)}


# ---------------------------------------------------------------------------
# GET /api/v1/configurations/{id}/versions/{version} — get specific version
# ---------------------------------------------------------------------------


@app.get("/api/v1/configurations/{config_id}/versions/{version}")
def get_configuration_version(config_id: str, version: int):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]
    if version not in cfg["versions"]:
        raise HTTPException(status_code=404, detail="Version not found")

    return {"data": _version_metadata(config_id, cfg, version), "links": {}}


# ---------------------------------------------------------------------------
# GET /api/v1/configurations/{id}/versions/{version}/content
# ---------------------------------------------------------------------------


@app.get("/api/v1/configurations/{config_id}/versions/{version}/content")
def get_configuration_version_content(config_id: str, version: int):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]
    if version not in cfg["versions"]:
        raise HTTPException(status_code=404, detail="Version not found")

    return cfg["versions"][version].get("content") or {}


# ---------------------------------------------------------------------------
# PUT /api/v1/configurations/{id} — update config (latest or ?version=N)
# ---------------------------------------------------------------------------


class MetadataUpdate(BaseModel):
    valid_from: str | None = None
    category: str = ""


class ConfigUpdate(BaseModel):
    metadata: MetadataUpdate | None = None
    content: dict[str, Any] | None = None


@app.put("/api/v1/configurations/{config_id}")
def update_configuration(
    config_id: str, body: ConfigUpdate = Body(...), version: int | None = None
):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]
    v = version if version is not None else _max_version(cfg)

    if not v or v not in cfg["versions"]:
        raise HTTPException(status_code=404, detail="Version not found")

    ver = cfg["versions"][v]
    if body.content is not None:
        ver["content"] = body.content
        ver["sha256sum"] = _content_hash(body.content)
    if body.metadata:
        if body.metadata.valid_from is not None:
            ver["valid_from"] = body.metadata.valid_from
        if body.metadata.category:
            cfg["category"] = body.metadata.category

    return {"data": _version_metadata(config_id, cfg, v), "links": {}}


# ---------------------------------------------------------------------------
# PUT /api/v1/configurations/{id}/versions/{version} — update specific version
# ---------------------------------------------------------------------------


@app.put("/api/v1/configurations/{config_id}/versions/{version}")
def update_configuration_version(
    config_id: str, version: int, body: ConfigUpdate = Body(...)
):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]
    if version not in cfg["versions"]:
        raise HTTPException(status_code=404, detail="Version not found")

    ver = cfg["versions"][version]
    if body.content is not None:
        ver["content"] = body.content
        ver["sha256sum"] = _content_hash(body.content)
    if body.metadata:
        if body.metadata.valid_from is not None:
            ver["valid_from"] = body.metadata.valid_from
        if body.metadata.category:
            cfg["category"] = body.metadata.category

    return {"data": _version_metadata(config_id, cfg, version), "links": {}}


# ---------------------------------------------------------------------------
# DELETE /api/v1/configurations/{id} — delete whole config or ?version=N
# ---------------------------------------------------------------------------


@app.delete("/api/v1/configurations/{config_id}")
def delete_configuration(config_id: str, version: int | None = None):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]

    if version is not None:
        if version not in cfg["versions"]:
            raise HTTPException(status_code=404, detail="Version not found")
        meta = _version_metadata(config_id, cfg, version)
        del cfg["versions"][version]
        if not cfg["versions"]:
            del configs[config_id]
        return {"data": [meta], "links": {}, "count": 1}
    else:
        results = [
            _version_metadata(config_id, cfg, v)
            for v in sorted(cfg["versions"].keys())
        ]
        del configs[config_id]
        return {"data": results, "links": {}, "count": len(results)}


# ---------------------------------------------------------------------------
# DELETE /api/v1/configurations/{id}/versions/{version} — delete one version
# ---------------------------------------------------------------------------


@app.delete("/api/v1/configurations/{config_id}/versions/{version}")
def delete_configuration_version(config_id: str, version: int):
    if config_id not in configs:
        raise HTTPException(status_code=404, detail="Configuration not found")

    cfg = configs[config_id]
    if version not in cfg["versions"]:
        raise HTTPException(status_code=404, detail="Version not found")

    meta = _version_metadata(config_id, cfg, version)
    del cfg["versions"][version]
    if not cfg["versions"]:
        del configs[config_id]

    return {"data": [meta], "links": {}, "count": 1}


# ---------------------------------------------------------------------------
# GET /api/v1/metadata — get distinct types, target_keys, categories
# ---------------------------------------------------------------------------


@app.get("/api/v1/metadata")
def get_metadata(
    type: str | None = None,
    target_key: str | None = None,
    category: str | None = None,
):
    types = set()
    target_keys = set()
    categories = set()

    for cfg in configs.values():
        if type and cfg["type"] != type:
            continue
        if target_key and cfg["target_key"] != target_key:
            continue
        if category and cfg.get("category", "") != category:
            continue
        types.add(cfg["type"])
        target_keys.add(cfg["target_key"])
        categories.add(cfg.get("category", ""))

    return {
        "data": {
            "types": sorted(types),
            "target_keys": sorted(target_keys),
            "categories": sorted(categories),
        },
        "links": {},
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
