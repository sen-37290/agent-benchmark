from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from agent_benchmark.models import ResolvedSpec, TaskResult


class BenchmarkPlugin(ABC):
    """Benchmark-owned preparation, canonical grading, and result interpretation."""

    name: str
    compatible_harnesses: frozenset[str]

    @abstractmethod
    def create_pool(self, output_path: Path, sampling: str | None, size: int | None) -> None:
        """Create a run-specific pool; no sampling arguments means the full benchmark."""

    @abstractmethod
    def prepare(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        """Materialize benchmark tasks on the execution machine."""

    @abstractmethod
    def grade(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        """Run the benchmark's canonical grader on the execution machine."""

    @abstractmethod
    def normalize(self, spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
        """Convert collected raw artifacts into the stable engine schema."""
