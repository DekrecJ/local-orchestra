from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class JobState(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    GENERATING = "generating"
    TESTING = "testing"
    CORRECTING = "correcting"
    AWAITING_APPROVAL = "awaiting_approval"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MODEL_ERROR = "model_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


TERMINAL_STATES = {
    JobState.PASSED,
    JobState.FAILED,
    JobState.CANCELLED,
    JobState.MODEL_ERROR,
    JobState.INFRASTRUCTURE_ERROR,
}


class JobKind(StrEnum):
    PYTHON_CODE = "python_code"
    CONVERSATION = "conversation"


class JobEvent(StrictModel):
    sequence: int = Field(ge=1)
    timestamp: datetime
    state: JobState
    event_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=1000)
    progress: int = Field(ge=0, le=100)
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderTrace(StrictModel):
    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=64)
    attempt: int = Field(ge=1, le=20)
    duration_seconds: float = Field(ge=0)
    outcome: Literal["success", "model_error", "infrastructure_error"]
    error_type: str | None = Field(default=None, max_length=128)


class CreateJobRequest(StrictModel):
    kind: JobKind = JobKind.PYTHON_CODE
    task: str = Field(min_length=1, max_length=6000)
    requires_approval: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 16:
            raise ValueError("metadata admite como máximo 16 entradas")
        if any(len(key) > 64 or len(item) > 256 for key, item in value.items()):
            raise ValueError("metadata supera los límites permitidos")
        return value


class ApprovalRequest(StrictModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=1000)


class MemoryCreateRequest(StrictModel):
    namespace: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")
    content: str = Field(min_length=1, max_length=16000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_memory_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 16 or len(json.dumps(value, ensure_ascii=False)) > 4096:
            raise ValueError("metadata de memoria supera los límites permitidos")
        return value


class MemorySearchRequest(StrictModel):
    namespace: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")
    query: str = Field(min_length=1, max_length=8000)
    limit: int = Field(default=5, ge=1, le=20)


class ErrorBody(StrictModel):
    code: str
    message: str
    category: Literal[
        "validation", "authentication", "authorization", "not_found",
        "conflict", "rate_limit", "model", "infrastructure", "sandbox",
        "internal",
    ]
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(StrictModel):
    error: ErrorBody


class JobCreatedResponse(StrictModel):
    job_id: str
    kind: JobKind
    state: JobState
    idempotency_key: str


class JobSnapshotResponse(StrictModel):
    job_id: str
    state: JobState
    progress: int = Field(ge=0, le=100)
    event_count: int | None = Field(default=None, ge=0)
    awaiting_approval: bool = False
    result_available: bool = False


class JobListItem(StrictModel):
    job_id: str
    state: JobState
    progress: int = Field(ge=0, le=100)
    temporal_status: str
    started_at: datetime
    closed_at: datetime | None = None


class JobListResponse(StrictModel):
    items: list[JobListItem]
    count: int = Field(ge=0)
    limit: int = Field(ge=1)


class EventListResponse(StrictModel):
    job_id: str
    items: list[JobEvent]
    count: int = Field(ge=0)


class HistoryEvent(StrictModel):
    event_id: int = Field(ge=1)
    event_type: str
    event_time: datetime


class HistoryResponse(StrictModel):
    job_id: str
    items: list[HistoryEvent]
    count: int = Field(ge=0)


class CancelResponse(StrictModel):
    job_id: str
    state: JobState
    cancel_requested: bool


class ApprovalResponse(StrictModel):
    job_id: str
    decision: Literal["approve", "reject"]
    accepted: bool


class JobResultResponse(StrictModel):
    job_id: str
    result: dict[str, Any]


class ArtifactInfo(StrictModel):
    name: str
    kind: Literal["source", "test"]
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ArtifactListResponse(StrictModel):
    job_id: str
    items: list[ArtifactInfo]
    count: int = Field(ge=0)


class ArtifactContentResponse(StrictModel):
    job_id: str
    name: str
    content: str


class MemoryCreatedResponse(StrictModel):
    memory_id: str
    namespace: str
    status: Literal["stored"]


class MemorySearchResponse(StrictModel):
    namespace: str
    items: list[dict[str, Any]]
    count: int = Field(ge=0)
