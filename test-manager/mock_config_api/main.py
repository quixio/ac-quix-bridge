"""
Mock Configuration API Service

This is a standalone FastAPI service that mocks the Quix Dynamic Configuration Manager API.
Used for local development and testing.

Extracted from backend/tests/conftest.py to be reusable across development and tests.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


app = FastAPI(
    title="Mock Configuration API",
    version="1.0.0",
    description="Mock implementation of Quix Configuration API for local development",
)

# In-memory storage for configurations
configurations: Dict[str, Dict[str, Any]] = {}


class ConfigurationMetadataInsert(BaseModel):
    """Configuration metadata for creation"""

    type: str
    target_key: str
    version: int | None = 1


class ConfigurationInsert(BaseModel):
    """Configuration data for creation"""

    metadata: ConfigurationMetadataInsert
    content: dict[str, Any] | None = None
    replace: bool | None = False


@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "ok", "service": "mock-config-api"}


@app.get("/api/v1/configurations")
def list_configurations():
    """List all configurations"""
    configs = []
    for config_id, config in configurations.items():
        configs.append({"id": config_id, "metadata": config["metadata"]})
    return {"data": configs}


@app.get("/api/v1/configurations/{config_id}")
def get_configuration(config_id: str):
    """Get configuration metadata by ID"""
    if config_id not in configurations:
        raise HTTPException(status_code=404, detail="Configuration not found")

    config = configurations[config_id]
    return {"data": {"id": config_id, "metadata": config["metadata"]}}


@app.get("/api/v1/configurations/{config_id}/content")
def get_configuration_content(config_id: str):
    """Get configuration content by ID"""
    if config_id not in configurations:
        raise HTTPException(status_code=404, detail="Configuration not found")

    config = configurations[config_id]
    return config.get("content", {})


@app.post("/api/v1/configurations")
def create_configuration(config_data: ConfigurationInsert):
    """Create a new configuration or create new version if replace=true"""
    now = datetime.now(timezone.utc)

    # If replace=true, check if a config with this target_key exists
    existing_config_id = None
    existing_version = 0

    if config_data.replace:
        for config_id, config in configurations.items():
            if config["metadata"]["target_key"] == config_data.metadata.target_key:
                existing_config_id = config_id
                existing_version = config["metadata"]["version"]
                break

    # If replace=true and config exists, update it (same ID, increment version)
    if existing_config_id:
        config_id = existing_config_id
        new_version = existing_version + 1

        configurations[config_id] = {
            "metadata": {
                "type": config_data.metadata.type,
                "target_key": config_data.metadata.target_key,
                "version": new_version,
                "created_at": configurations[config_id]["metadata"]["created_at"],
                "updated_at": now,
            },
            "content": config_data.content,
        }

        return {
            "data": {
                "id": config_id,
                "metadata": {
                    "type": config_data.metadata.type,
                    "target_key": config_data.metadata.target_key,
                    "version": new_version,
                    "created_at": configurations[config_id]["metadata"]["created_at"].isoformat(),
                    "updated_at": now.isoformat(),
                },
            }
        }
    else:
        # Create new configuration
        config_id = str(uuid.uuid4())

        configurations[config_id] = {
            "metadata": {
                "type": config_data.metadata.type,
                "target_key": config_data.metadata.target_key,
                "version": config_data.metadata.version or 1,
                "created_at": now,
                "updated_at": now,
            },
            "content": config_data.content,
        }

        return {
            "data": {
                "id": config_id,
                "metadata": {
                    "type": config_data.metadata.type,
                    "target_key": config_data.metadata.target_key,
                    "version": config_data.metadata.version or 1,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            }
        }


@app.put("/api/v1/configurations/{config_id}")
def update_configuration(config_id: str, update_data: dict):
    """Update an existing configuration"""
    if config_id not in configurations:
        raise HTTPException(status_code=404, detail="Configuration not found")

    config = configurations[config_id]
    now = datetime.now(timezone.utc)

    # Update content if provided
    if "content" in update_data:
        config["content"] = update_data["content"]

    # Update metadata timestamp
    config["metadata"]["updated_at"] = now

    return {"data": {"id": config_id, "metadata": config["metadata"]}}


@app.delete("/api/v1/configurations/{config_id}")
def delete_configuration(config_id: str) -> Dict[str, str]:
    """Delete a configuration"""
    if config_id not in configurations:
        raise HTTPException(status_code=404, detail="Configuration not found")

    del configurations[config_id]
    return {"message": "Configuration deleted successfully"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
