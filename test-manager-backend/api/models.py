from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from enum import Enum
from math import ceil

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .utils import now


# Generic type for paginated responses
T = TypeVar("T")


class PaginationParams(BaseModel):
    """Pagination parameters for list endpoints."""

    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(default=20, description="Number of items per page")

    @field_validator("page_size")
    @classmethod
    def validate_page_size(cls, v: int) -> int:
        """Validate that page_size is one of the allowed values."""
        allowed_sizes = [10, 20, 50, 100, 200]
        if v not in allowed_sizes:
            raise ValueError(f"page_size must be one of {allowed_sizes}")
        return v


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""

    items: list[T]
    total: int = Field(description="Total number of items across all pages")
    page: int = Field(description="Current page number")
    page_size: int = Field(description="Number of items per page")
    total_pages: int = Field(description="Total number of pages")

    @classmethod
    def create(
        cls, items: list[T], total: int, page: int, page_size: int
    ) -> "PaginatedResponse[T]":
        """Helper method to create a paginated response."""
        total_pages = ceil(total / page_size) if page_size > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )


class TestStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"


class DeviceReference(BaseModel):
    """Reference to a Device with its version snapshot."""

    device_id: str
    device_version: str | None = (
        None  # UUID of DeviceJournalEntry, set when test starts
    )


class SessionInfo(BaseModel):
    """A session linked to a test, with track and car info from AC."""

    session_id: str
    track: str
    car_model: str


class Test(BaseModel):
    """Represents a test / experiment record."""

    test_id: str = Field(..., alias="_id")
    experiment_id: str
    pc_device_id: str
    test_rig_device_id: str
    environment_id: str
    driver: str
    requirements: str = ""
    sessions: list[SessionInfo] = []
    # Resolved display names (populated by API, not stored in DB)
    pc_device_name: str | None = None
    test_rig_device_name: str | None = None
    environment_name: str | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    config_id: str
    config_type: str | None = None
    target_key: str | None = None
    config_version: int | None = None


class TestCreate(BaseModel):
    """Request model for creating a Test. ID is auto-generated."""

    experiment_id: str = Field(..., min_length=1)
    pc_device_id: str = Field(..., min_length=1)
    test_rig_device_id: str = Field(..., min_length=1)
    environment_id: str = Field(..., min_length=1)
    driver: str = Field(..., min_length=1)
    requirements: str = ""


class TestUpdate(BaseModel):
    """Request model for updating a Test."""

    experiment_id: str | None = None
    pc_device_id: str | None = None
    test_rig_device_id: str | None = None
    environment_id: str | None = None
    driver: str | None = None
    requirements: str | None = None


class TestQuery(PaginationParams):
    """Query parameters for filtering Tests."""

    experiment_id: str | None = None
    environment_id: str | None = None
    driver: str | None = None
    q: str | None = None


class TestFullData(BaseModel):
    """A test with its related data."""

    test: Test
    logbook: list["LogbookEntry"]


class LogbookEntry(BaseModel):
    """Represents a single logbook entry for a test."""

    id: str = Field(..., alias="_id")
    test_id: str
    session_id: str | None = None  # None = test-wide note
    created_at: datetime = Field(default_factory=now)
    content: str


class LogbookEntryCreate(BaseModel):
    """Request model for creating a logbook entry."""

    content: str = Field(..., min_length=1)
    session_id: str | None = None


class LogbookEntryUpdate(BaseModel):
    """Request model for updating a logbook entry."""

    content: str | None = Field(default=None, min_length=1)
    session_id: str | None = None  # explicit set/change/clear


# ============================================================================
# Device Models
# ============================================================================


class DeviceStatus(str, Enum):
    """Device operational status."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class DeviceCategory(str, Enum):
    """Device category."""

    PC = "pc"
    TEST_RIG = "test_rig"


class Device(BaseModel):
    """Represents a device — either a PC (hostname) or a Test Rig (steering wheel)."""

    device_id: str = Field(..., alias="_id")
    category: DeviceCategory
    name: str
    status: DeviceStatus = DeviceStatus.ACTIVE
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class DeviceCreate(BaseModel):
    """Request model for creating a Device. ID is auto-generated."""

    category: DeviceCategory
    name: str = Field(..., min_length=1, description="Device name")
    status: DeviceStatus = DeviceStatus.ACTIVE


class DeviceUpdate(BaseModel):
    """Request model for updating a Device."""

    name: str | None = Field(default=None, min_length=1)
    category: DeviceCategory | None = None
    status: DeviceStatus | None = None


class DeviceQuery(PaginationParams):
    """Query parameters for filtering Devices."""

    category: DeviceCategory | None = None
    status: DeviceStatus | None = None
    q: str | None = None


# ============================================================================
# Lookup Table Models - Phase 2
# ============================================================================


class SampleType(BaseModel):
    """Represents a sample type lookup value."""

    id: str = Field(..., alias="_id")
    sample_type: str


class Location(BaseModel):
    """Represents a location lookup value."""

    id: str = Field(..., alias="_id")
    location: str


class ProductCategory(BaseModel):
    """Represents a product category lookup value."""

    product_category: str = Field(..., alias="_id")  # Business key
    name: str  # Human-readable name


class Product(BaseModel):
    """Represents a product in the catalog."""

    id: str = Field(..., alias="_id")
    manufacturer: str
    product_category: str  # References ProductCategory._id
    product_name: str


# ============================================================================
# Application Settings Models
# ============================================================================


class IntegrationSettings(BaseModel):
    """Represents the integration settings stored in MongoDB."""

    # Configurations - Dynamic Configuration Manager
    config_api_deployment: "DeploymentReference | None" = None
    config_api_is_fallback: bool = False

    # Measurements - Query UI deployment and topic
    measurements_deployment: "DeploymentReference | None" = None  # Query UI
    measurements_topic: "TopicReference | None" = None  # Selected topic with workspace
    measurements_is_fallback: bool = False

    # Analytics - Marimo/Analytics deployment
    analytics_deployment: "DeploymentReference | None" = None
    analytics_is_fallback: bool = False

    updated_at: datetime | None = None
    updated_by: str | None = None


class IntegrationSettingsUpdate(BaseModel):
    """Represents the updatable fields for integration settings."""

    # Config API
    config_api_deployment: "DeploymentReference | None" = None

    # Measurements
    measurements_deployment: "DeploymentReference | None" = None
    measurements_topic: "TopicReference | None" = None

    # Analytics
    analytics_deployment: "DeploymentReference | None" = None


class Topic(BaseModel):
    """Represents a Quix topic from Portal API (legacy, simple format)."""

    id: str
    name: str


class Workspace(BaseModel):
    """Represents a Quix workspace from Portal API."""

    id: str
    name: str


class TopicInfo(BaseModel):
    """Topic information from Portal API with full details."""

    topic_id: str = Field(..., alias="topicId")
    name: str
    workspace_id: str = Field(..., alias="workspaceId")
    status: str | None = None

    model_config = {"populate_by_name": True}


class TopicReference(BaseModel):
    """Reference to a selected topic stored in settings."""

    topic_name: str
    workspace_id: str
    workspace_name: str | None = None


# ============================================================================
# Portal API Models - Deployment Selector
# ============================================================================


class Repository(BaseModel):
    """Represents a Quix Project from Portal API."""

    repository_id: str = Field(..., alias="repositoryId")
    name: str

    model_config = {"populate_by_name": True}


class WorkspaceDetails(BaseModel):
    """Workspace/Environment with extended details from Portal API."""

    workspace_id: str = Field(..., alias="workspaceId")
    name: str
    repository_id: str = Field(..., alias="repositoryId")
    environment_name: str = Field(..., alias="environmentName")
    status: str

    model_config = {"populate_by_name": True}


class DeploymentInfo(BaseModel):
    """Deployment information from Portal API."""

    deployment_id: str = Field(..., alias="deploymentId")
    name: str
    status: str
    public_url: str | None = Field(None, alias="publicUrl")
    embedded_view_url: str | None = None  # From plugin.embeddedViewUrl
    service_name: str | None = None  # From network.serviceName
    public_access: bool = Field(False, alias="publicAccess")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class Driver(BaseModel):
    """Represents a driver (operator) in the system."""

    driver_id: str = Field(..., alias="_id")
    name: str
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class DriverCreate(BaseModel):
    """Request model for creating a Driver. ID is auto-generated."""

    name: str = Field(..., min_length=1, description="Driver name")


class DriverUpdate(BaseModel):
    """Request model for updating a Driver."""

    name: str | None = Field(default=None, min_length=1)


class DriverQuery(PaginationParams):
    """Query parameters for filtering Drivers."""

    name: str | None = None
    q: str | None = None


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class EnvironmentStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Environment(BaseModel):
    """Represents a test environment (location)."""

    environment_id: str = Field(..., alias="_id")
    name: str
    location: str | None = None
    status: EnvironmentStatus = EnvironmentStatus.ACTIVE
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class EnvironmentCreate(BaseModel):
    """Request model for creating an Environment. ID is auto-generated."""

    name: str = Field(..., min_length=1, description="Environment name")
    location: str | None = None
    status: EnvironmentStatus = EnvironmentStatus.ACTIVE


class EnvironmentUpdate(BaseModel):
    """Request model for updating an Environment."""

    name: str | None = Field(default=None, min_length=1)
    location: str | None = None
    status: EnvironmentStatus | None = None


class EnvironmentQuery(PaginationParams):
    """Query parameters for filtering Environments."""

    name: str | None = None
    location: str | None = None
    status: EnvironmentStatus | None = None
    q: str | None = None


class DeploymentReference(BaseModel):
    """Reference to a selected deployment stored in settings."""

    deployment_id: str
    workspace_id: str
    deployment_name: str
    public_url: str | None = None
    embedded_view_url: str | None = None
    internal_url: str | None = None


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


class BestLapEntry(BaseModel):
    """A single row in the leaderboard: one driver's best lap within a
    (track, car, experiment) scope.

    `best_lap_ms` is the fastest per-lap timestamp delta (in milliseconds)
    observed across every lap-partition for this (track, car, experiment,
    driver). See `routes/leaderboard.py` for the aggregation details, including
    the "drop highest lap per session" guard that filters out the in-progress
    lap of each session.

    The `session_id` / `lap_number` / `achieved_at` fields are reserved for
    V2 extensions (deep-link to Compare, date achieved column) and are always
    `None` in V1.
    """

    track: str
    car: str
    experiment: str
    driver: str
    best_lap_ms: int
    session_id: str | None = None
    lap_number: int | None = None
    achieved_at: datetime | None = None


# ============================================================================
# Analysis Models
# ============================================================================


class KpiValue(BaseModel):
    """One measurable KPI surfaced by the AI agent."""

    name: str  # opaque string — e.g. "best_lap"
    value: float | str
    unit: str | None = None
    notes: str | None = None
    session_id: str | None = None  # v2: attribution in test-wide mode


class RequirementCheck(BaseModel):
    """One requirement extracted from Test.requirements + verdict."""

    requirement: str  # free text echoing Test.requirements
    met: bool | None = None  # tri-state: true / false / None (undetermined)
    evidence: str | None = None


class Anomaly(BaseModel):
    """One detected event of note (brake spike, off-track, telemetry gap, ...)."""

    severity: Literal["info", "warn", "error"]
    kind: str  # opaque string — e.g. "brake_spike"
    lap: int | None = None
    time_ms: int | None = None
    description: str
    evidence: str | None = None
    session_id: str | None = None  # v2: attribution in test-wide mode


class Analysis(BaseModel):
    """Persisted analysis result. One doc per click of Analyze."""

    id: str = Field(..., alias="_id")  # uuid4 string
    schema_version: int = 2  # v2 introduces optional session_id (null = test-wide)
    test_id: str
    session_id: str | None  # null on test-wide rows
    status: Literal[
        "pending",
        "running",
        "fetching",
        "analyzing",
        "saving",
        "complete",
        "failed",
    ]
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    # Quix.AI session linkage (for debug)
    quix_session_id: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache_create: int | None = None
    tokens_cache_read: int | None = None
    duration_ms: int | None = None

    # Failure info (only set when status="failed")
    error: str | None = None
    error_kind: Literal["timeout", "agent", "validation", "orphan"] | None = None

    # Content — only populated on save_analysis MCP call
    kpis: list[KpiValue] = []
    requirements_check: list[RequirementCheck] = []
    logbook_refs: list[str] = []
    anomalies: list[Anomaly] = []
    summary_md: str = ""  # required at save time; "" while pending
    extra: dict[str, Any] = {}  # freeform escape hatch

    model_config = ConfigDict(populate_by_name=True)


class AnalysisCreate(BaseModel):
    """Request body for POST /api/v1/analyses.

    session_id is optional: null = test-wide (analyze every session of the test).
    """

    test_id: str = Field(..., min_length=1)
    session_id: str | None = None


class AnalysisListQuery(PaginationParams):
    """Query parameters for GET /api/v1/analyses."""

    test_id: str | None = None
    session_id: str | None = None
    status: Literal["complete", "failed", "in_progress"] | None = None


class SaveAnalysisPayload(BaseModel):
    """MCP write tool input — agent submits this via save_analysis."""

    analysis_id: str
    kpis: list[KpiValue] = []
    requirements_check: list[RequirementCheck] = []
    logbook_refs: list[str] = []
    anomalies: list[Anomaly] = []
    summary_md: str = Field(..., min_length=1)
    extra: dict[str, Any] = {}
