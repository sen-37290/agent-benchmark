from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_benchmark.config.schema import ResolvedSpec


@dataclass(frozen=True)
class SubjectAgentInvocation:
    model_name: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    process_environment: dict[str, str] = field(default_factory=dict)


class SubjectAgentAdapter(ABC):
    name: str

    @abstractmethod
    def invocation(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        api_key: str,
    ) -> SubjectAgentInvocation: ...
