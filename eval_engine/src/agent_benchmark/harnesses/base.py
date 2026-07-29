from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from agent_benchmark.config.schema import ResolvedSpec


class HarnessAdapter(ABC):
    """Executes a subject against already-prepared benchmark tasks."""

    name: str

    @abstractmethod
    def execute(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        cache_root: Path,
        secrets: dict[str, str],
    ) -> None:
        """Execute the harness and preserve all raw output under run_dir/artifacts."""
