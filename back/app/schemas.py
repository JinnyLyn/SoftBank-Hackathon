from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectOut(APIModel):
    id: UUID
    name: str
    source_filename: str
    source_sha256: str
    source_size_bytes: int
    source_type: Literal["zip", "github"]
    source_url: str | None
    source_ref: str | None
    created_at: datetime


class GitHubProjectIn(APIModel):
    name: str = Field(min_length=1, max_length=120)
    repository_url: str = Field(min_length=1, max_length=2048)
    ref: str | None = Field(default=None, min_length=1, max_length=255)


class ConnectionIn(APIModel):
    provider: Literal["aws"]
    name: str = Field(min_length=1, max_length=120)
    fields: dict[str, str] = Field(default_factory=dict)


class ConnectionOut(APIModel):
    id: UUID
    provider: Literal["aws"]
    name: str
    status: Literal["connected", "pending", "error"]
    detail: str
    error: str | None = None
    setupUrl: str | None = None
    checkedAt: datetime
    fields: dict[str, str]


class FrontDeployStatus(APIModel):
    state: Literal["running", "success", "failed"]
    log: list[str]
    url: str | None = None
    diagnosis: dict[str, Any] | None = None


class ProjectListOut(APIModel):
    items: list[ProjectOut]
    next_cursor: str | None = None


class AnalysisIn(APIModel):
    schema_version: str = Field(min_length=1, max_length=32)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result: dict[str, Any]

    @field_validator("result")
    @classmethod
    def result_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json

        if len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 128 * 1024:
            raise ValueError("analysis result must be at most 128 KiB")
        return value


class AnalysisOut(APIModel):
    id: UUID
    project_id: UUID
    schema_version: str
    source_sha256: str
    result: dict[str, Any]
    created_at: datetime


class CostEstimate(APIModel):
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=4)
    currency: Literal["USD"]
    period: Literal["month"] = "month"
    pricing_as_of: str = Field(min_length=4, max_length=32)


class PlanIn(APIModel):
    project_id: UUID
    analysis_id: UUID | None = None
    target: Literal["aws"]
    module_id: str = Field(min_length=1, max_length=100)
    variables: dict[str, Any]
    summary: str = Field(min_length=1, max_length=8000)
    cost_estimate: CostEstimate | None = None
    terraform_plan_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("variables")
    @classmethod
    def variables_are_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json

        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ValueError("plan variables must be at most 64 KiB")
        return value


class PlanOut(APIModel):
    id: UUID
    project_id: UUID
    analysis_id: UUID | None
    target: str
    operation_type: Literal["deploy", "rollback"]
    rollback_from_deployment_id: UUID | None = None
    rollback_to_deployment_id: UUID | None = None
    module_id: str
    variables: dict[str, Any]
    summary: str
    cost_estimate: CostEstimate | None
    terraform_plan_sha256: str | None
    terraform_plan_summary: dict[str, Any] | None = None
    terraform_plan_ready: bool
    fingerprint: str
    status: Literal["awaiting_approval", "approved", "consumed", "superseded"]
    created_at: datetime
    approved_at: datetime | None


class ApproveIn(APIModel):
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class DeploymentCreateIn(APIModel):
    plan_id: UUID
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class RollbackRequestIn(APIModel):
    """The healthy deployment selected before generating a fresh rollback plan."""

    expected_target_deployment_id: UUID


class RollbackPlanSummaryIn(APIModel):
    terraform_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary: dict[str, Any]

    @field_validator("summary")
    @classmethod
    def summary_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json

        if len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 128 * 1024:
            raise ValueError("terraform plan summary must be at most 128 KiB")
        return value


class RollbackCandidateOut(APIModel):
    rollback_available: bool
    reason: Literal["available", "first_deployment", "no_previous_healthy", "not_failed"]
    target_deployment_id: UUID | None = None
    target_plan_id: UUID | None = None


class DeploymentOut(APIModel):
    id: UUID
    plan_id: UUID
    project_id: UUID
    target: str
    operation_type: Literal["deploy", "rollback"]
    rollback_from_deployment_id: UUID | None = None
    rollback_to_deployment_id: UUID | None = None
    status: Literal[
        "queued", "provisioning", "deploying", "healthy", "failed", "rolling_back", "rolled_back"
    ]
    url: str | None
    created_at: datetime
    updated_at: datetime


class DeploymentEventOut(APIModel):
    id: int
    level: Literal["info", "warning", "error"]
    event_type: str
    message: str
    details: dict[str, Any] | None
    created_at: datetime


class DeploymentDetailOut(DeploymentOut):
    events: list[DeploymentEventOut]


class WorkerEventIn(APIModel):
    status: Literal["provisioning", "deploying", "healthy", "failed", "rolling_back", "rolled_back"]
    level: Literal["info", "warning", "error"] = "info"
    event_type: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=4000)
    details: dict[str, Any] | None = None
    url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def details_are_bounded(self) -> "WorkerEventIn":
        import json

        if self.details is not None:
            try:
                encoded_details = json.dumps(self.details, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except ValueError as exc:
                raise ValueError("event details must be valid JSON") from exc
            if len(encoded_details) > 32 * 1024:
                raise ValueError("event details must be at most 32 KiB")
        if self.url:
            parsed = urlsplit(self.url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or any(char.isspace() for char in self.url)
            ):
                raise ValueError("url must be an HTTP(S) address without credentials, query, or fragment")
        return self
