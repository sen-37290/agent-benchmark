from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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
    CANCELLED = "cancelled"


class StageRecord(BaseModel):
    status: StageStatus = StageStatus.PENDING
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class RunState(BaseModel):
    run_id: str
    cancelled_at: datetime | None = None
    stages: dict[StageName, StageRecord] = Field(
        default_factory=lambda: {stage: StageRecord() for stage in PIPELINE_STAGES}
    )

    @property
    def complete(self) -> bool:
        return all(record.status == StageStatus.SUCCEEDED for record in self.stages.values())

    @property
    def cancelled(self) -> bool:
        return self.cancelled_at is not None
