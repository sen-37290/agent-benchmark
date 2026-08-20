from pathlib import Path

from agent_benchmark.benchmarks.agent_perf.pool import generate_pool
from agent_benchmark.benchmarks.agent_perf.results import normalize_results
from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.run.result import TaskResult


class AgentPerf(BenchmarkPlugin):
    """Agent Performance (agent-perf) inference workload benchmark plugin."""

    name = "agent_perf"
    compatible_harnesses = frozenset(["agent-perf-native"])

    def create_pool(self, output_path: Path, sampling: str | None, size: int | None) -> None:
        generate_pool(output_path, sampling, size)

    def prepare(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        (run_dir / "artifacts" / "agent_perf").mkdir(parents=True, exist_ok=True)

    def grade(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        # Metrics collected during execution trace replay
        pass

    def normalize(self, spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
        return normalize_results(spec, run_dir)
