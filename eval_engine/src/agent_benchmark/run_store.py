from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from agent_benchmark.errors import ConfigurationError
from agent_benchmark.models import (
    ResolvedSpec,
    RunState,
    StageName,
    StageStatus,
    UserRequest,
)


class RunStore:
    def __init__(self, root: Path, run_id: str):
        self.root = root.resolve()
        self.run_id = run_id
        self.path = self.root / run_id

    def create(self, request: UserRequest, spec: ResolvedSpec, generated_pool: Path) -> None:
        if self.path.exists():
            raise ConfigurationError(f"run already exists: {self.run_id}")
        (self.path / "logs").mkdir(parents=True)
        (self.path / "artifacts").mkdir()
        (self.path / "results").mkdir()
        (self.path / "report").mkdir()
        (self.path / "inputs").mkdir()
        shutil.copy2(generated_pool, self.path / "inputs" / "pool.json")
        self._write_yaml(self.path / "request.yaml", request.model_dump(mode="json"))
        self._write_yaml(self.path / "resolved.yaml", spec.model_dump(mode="json"))
        self.save_state(RunState(run_id=self.run_id))
        self.event("run_created", {"run_id": self.run_id})

    def load_spec(self) -> ResolvedSpec:
        path = self.path / "resolved.yaml"
        if not path.exists():
            raise ConfigurationError(f"run not found: {self.run_id}")
        return ResolvedSpec.model_validate(yaml.safe_load(path.read_text()))

    def load_state(self) -> RunState:
        path = self.path / "state.json"
        if not path.exists():
            raise ConfigurationError(f"run state not found: {self.run_id}")
        return RunState.model_validate_json(path.read_text())

    def save_state(self, state: RunState) -> None:
        self._atomic_write(self.path / "state.json", state.model_dump_json(indent=2) + "\n")

    def start_stage(self, stage: StageName) -> None:
        state = self.load_state()
        record = state.stages[stage]
        record.status = StageStatus.RUNNING
        record.attempts += 1
        record.started_at = datetime.now(UTC)
        record.finished_at = None
        record.error = None
        self.save_state(state)
        self.event("stage_started", {"stage": stage, "attempt": record.attempts})

    def finish_stage(self, stage: StageName) -> None:
        state = self.load_state()
        record = state.stages[stage]
        record.status = StageStatus.SUCCEEDED
        record.finished_at = datetime.now(UTC)
        self.save_state(state)
        self.event("stage_succeeded", {"stage": stage})

    def fail_stage(self, stage: StageName, error: Exception) -> None:
        state = self.load_state()
        record = state.stages[stage]
        record.status = StageStatus.FAILED
        record.finished_at = datetime.now(UTC)
        record.error = f"{type(error).__name__}: {error}"
        self.save_state(state)
        self.event("stage_failed", {"stage": stage, "error": record.error})

    def event(self, kind: str, details: dict[str, Any]) -> None:
        payload = {"timestamp": datetime.now(UTC).isoformat(), "event": kind, **details}
        with (self.path / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")

    @staticmethod
    def _write_yaml(path: Path, data: dict[str, Any]) -> None:
        RunStore._atomic_write(path, yaml.safe_dump(data, sort_keys=False))

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
