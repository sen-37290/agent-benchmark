from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)


class UserRequest(BaseModel):
    benchmark: str
    sampling: str | None = None
    size: int | None = Field(default=None, ge=1)
    model: str
    agent: str | None = None
    reasoning_effort: str | None = None
    provider: str
    provider_route: str | None = None
    byok: bool = False
    workers: PositiveInt
    budget_usd: PositiveFloat | None = None
    no_budget_limit: bool = False
    per_task_cost_limit_usd: PositiveFloat | None = None
    no_timeout: bool = False
    # Scale Harbor's per-task agent deadline instead of removing it. `--no-timeout` is the
    # unbounded extreme of the same knob; see the note on ExecutionSpec.
    agent_timeout_multiplier: PositiveFloat | None = None
    error_retries: int | None = Field(default=None, ge=0)
    target: str = "primary"
    label: str | None = None
    # Name of the environment variable holding this experiment's key. The model profile still
    # decides the canonical name the subject agent reads; this only says where to read it from.
    api_key_from: str | None = None
    no_cleanup: bool = False

    @field_validator("benchmark", "model", "provider", "target")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("agent", "reasoning_effort", "label", "api_key_from")
    @classmethod
    def optional_nonempty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def validate_byok(self) -> UserRequest:
        if self.provider not in {"openrouter", "friendli", "anthropic", "openai"}:
            raise ValueError("--provider must be openrouter, friendli, anthropic, or openai")
        if self.byok and self.provider != "friendli" and not self.provider_route:
            raise ValueError("--byok requires --provider friendli or --provider-route")
        if (self.sampling is None) != (self.size is None):
            raise ValueError("--sampling and --size must be provided together")
        if (self.budget_usd is None) == (not self.no_budget_limit):
            raise ValueError("provide exactly one of --budget-usd or --no-budget-limit")
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
    # Where the orchestrator reads the secret from. Defaults to api_key_env; set per experiment so
    # ten concurrent runs can each use their own key while the subject agent still sees the
    # canonical provider variable name.
    api_key_source_env: str = ""
    effort_path: str
    reasoning_effort: str | None = None
    provider_route: str | None = None
    byok: bool = False
    config: dict[str, Any]

    @model_validator(mode="after")
    def default_key_source(self) -> ModelSpec:
        if not self.api_key_source_env:
            # Older resolved specs have no source field; they read the canonical variable.
            object.__setattr__(self, "api_key_source_env", self.api_key_env)
        return self


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
    model_config = ConfigDict(extra="forbid")

    profile: str
    backend: Literal["ssh"] = "ssh"
    # The default keeps resolved specs from the original single-VM implementation resumable.
    mode: Literal["primary", "backup"] = "primary"
    host_env: str
    remote_root: str
    cache_root: str


class ExecutionSpec(BaseModel):
    workers: PositiveInt
    environment: str = "docker"
    #: Remove Harbor's agent deadline entirely (multiplier ``inf``).
    #:
    #: Use with care. Terminus 2's ``max_episodes`` defaults to 1,000,000, so with no deadline
    #: the ONLY bound left on a task is the per-task dollar cap -- and on a cheap model that is
    #: barely a bound at all. A gpt-5.6-luna task observed burning $0.06/hour would need ~320
    #: hours to reach a $20 cap. Prefer ``agent_timeout_multiplier`` unless unbounded really is
    #: what you want.
    no_timeout: bool = False
    #: Multiply Harbor's per-task agent deadline (1.0 leaves the official limits unchanged).
    #: The bounded way to give slow tasks more room: 6.0 turns the 900/1800/3600s limits into
    #: 1.5/3/6 hours while still guaranteeing the task ends.
    agent_timeout_multiplier: float | None = None
    error_retries: int = Field(default=0, ge=0)
    # Retain the remote run workspace after a successful pipeline instead of removing it.
    no_cleanup: bool = False


class BudgetSpec(BaseModel):
    total_usd: PositiveFloat | None
    per_task_usd: PositiveFloat


class ProvenanceSpec(BaseModel):
    engine_version: str
    source_revision: str | None = None
    source_dirty: bool = False
    lock_sha256: str | None = None


class ResolvedSpec(BaseModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    # Operator-facing experiment name; the join key between experiments.yaml, the VM and the
    # fleet monitor. Absent on runs created before labels existed.
    label: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    benchmark: BenchmarkSpec
    model: ModelSpec
    target: TargetSpec
    execution: ExecutionSpec
    budget: BudgetSpec
    provenance: ProvenanceSpec
