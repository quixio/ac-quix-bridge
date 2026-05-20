"""Pydantic models for the QuixLake Querier agent's structured plan output.

Standalone — no project-local imports — so the file is portable and the
contract is shared verbatim with the standalone telemetry-chat service.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Trace(BaseModel):
    # `extra="ignore"` on Trace only — leaves room for the agent contract to
    # grow optional annotation fields (e.g. color_hint) without breaking
    # already-deployed services.
    model_config = ConfigDict(extra="ignore")

    session_id: str
    lap: int
    driver: str
    carModel: str
    track: str
    experiment: str
    environment: str
    test_rig: str


class PlotPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["plot"]
    title: str = ""
    signals: list[str] = Field(min_length=1)
    traces: list[Trace] = Field(min_length=1)


class ClarifyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["clarify"]
    question: str
    options: list[str] = []


AgentPlan = Annotated[PlotPlan | ClarifyPlan, Field(discriminator="type")]
