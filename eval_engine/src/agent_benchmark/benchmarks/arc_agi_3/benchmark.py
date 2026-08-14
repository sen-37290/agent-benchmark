from pathlib import Path

from agent_benchmark.benchmarks.arc_agi_3.pool import generate_pool
from agent_benchmark.benchmarks.arc_agi_3.results import normalize_results
from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.run.result import TaskResult


class ArcAgi3(BenchmarkPlugin):
    """ARC-AGI-3 interactive reasoning benchmark plugin."""

    name = "arc_agi_3"
    compatible_harnesses = frozenset(["arc-agi-3-native"])

    def create_pool(self, output_path: Path, sampling: str | None, size: int | None) -> None:
        generate_pool(output_path, sampling, size)

    def prepare(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        (run_dir / "artifacts" / "arc_agi_3").mkdir(parents=True, exist_ok=True)

    def grade(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        # Inline grading recorded per environment step in result.json
        pass

    def normalize(self, spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
        return normalize_results(spec, run_dir)
