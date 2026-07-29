from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PositiveFloat, PositiveInt, field_validator, model_validator


class StageName(StrEnum):
    DEPLOY = "deploy"
    PREPARE = "prepare"
    EXECUTE = "execute"
    GRADE = "grade"
    COLLECT = "collect"
    NORMALIZE = "normalize"
    REPORT = "report"
    CLEANUP = "cleanup"


PIPELINE_STAGES = tuple(StageName)


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class UserRequest(BaseModel):
    benchmark: str
    sampling: str | None = None
    size: int | None = Field(default=None, ge=1)
    model: str
    reasoning_effort: str
    provider: str
    provider_route: str | None = None
    byok: bool = False
    workers: PositiveInt
    budget_usd: PositiveFloat
    per_task_cost_limit_usd: PositiveFloat = 5.0
    target: str = "fixed-vm"

    @field_validator("benchmark", "model", "reasoning_effort", "provider", "target")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def validate_byok(self) -> UserRequest:
        if self.provider not in {"openrouter", "friendli", "anthropic"}:
            raise ValueError("--provider must be openrouter, friendli, or anthropic")
        if self.byok and self.provider != "friendli" and not self.provider_route:
            raise ValueError("--byok requires --provider friendli or --provider-route")
        if (self.sampling is None) != (self.size is None):
            raise ValueError("--sampling and --size must be provided together")
        return self


class ModelSpec(BaseModel):
    profile: str
    model_id: str
    subject_agent: str
    subject_agent_version: str
    model_class: str
    provider: str
    api: str
    api_key_env: str
    effort_path: str
    reasoning_effort: str
    provider_route: str | None = None
    byok: bool = False
    config: dict[str, Any]


class BenchmarkSpec(BaseModel):
    profile: str
    plugin: str
    harness: str
    dataset_id: str
    sampling: str
    sample_size: int
    pool_path: str
    pool_sha256: str
    grading: str
    settings: dict[str, Any] = Field(default_factory=dict)


class TargetSpec(BaseModel):
    profile: str
    backend: Literal["ssh"] = "ssh"
    host_env: str
    remote_root: str
    cache_root: str


class ExecutionSpec(BaseModel):
    workers: PositiveInt
    environment: str = "docker"


class BudgetSpec(BaseModel):
    total_usd: PositiveFloat
    per_task_usd: PositiveFloat


class ProvenanceSpec(BaseModel):
    engine_version: str
    source_revision: str | None = None
    source_dirty: bool = False
    lock_sha256: str | None = None


class ResolvedSpec(BaseModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    benchmark: BenchmarkSpec
    model: ModelSpec
    target: TargetSpec
    execution: ExecutionSpec
    budget: BudgetSpec
    provenance: ProvenanceSpec


class StageRecord(BaseModel):
    status: StageStatus = StageStatus.PENDING
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class RunState(BaseModel):
    run_id: str
    stages: dict[StageName, StageRecord] = Field(
        default_factory=lambda: {stage: StageRecord() for stage in PIPELINE_STAGES}
    )

    @property
    def complete(self) -> bool:
        return all(record.status == StageStatus.SUCCEEDED for record in self.stages.values())


class TaskResult(BaseModel):
    run_id: str
    task_id: str
    status: Literal["completed", "error", "missing"]
    metrics: dict[str, float | int | bool | None] = Field(default_factory=dict)
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    duration_seconds: float | None = None
    error_type: str | None = None
    raw_artifacts: list[str] = Field(default_factory=list)


def default_runs_root() -> Path:
    return Path.cwd() / "runs"
