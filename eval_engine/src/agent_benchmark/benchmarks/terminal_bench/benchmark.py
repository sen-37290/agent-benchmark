from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.benchmarks.terminal_bench.pool import catalog, create_pool
from agent_benchmark.benchmarks.terminal_bench.results import (
    normalize,
    pool_ids,
    validate_and_summarize,
)
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.result import TaskResult


class TerminalBench(BenchmarkPlugin):
    name = "terminal_bench"
    compatible_harnesses = frozenset({"harbor"})

    def create_pool(self, output_path: Path, sampling: str | None, size: int | None) -> None:
        create_pool(output_path, sampling, size)

    def prepare(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        del cache_root
        source = catalog()
        pool = json.loads((run_dir / spec.benchmark.pool_path).read_text())
        expected_digest = str(spec.benchmark.settings["dataset_digest"])
        if (
            source["dataset_digest"] != expected_digest
            or pool.get("dataset_digest") != expected_digest
        ):
            raise StageError("Terminal-Bench dataset digest does not match the pinned catalog")
        known = {task["id"] for task in source["tasks"]}
        unknown = set(pool_ids(spec, run_dir)) - known
        if unknown:
            unknown_text = ", ".join(sorted(unknown))
            raise StageError(f"Terminal-Bench pool contains unknown tasks: {unknown_text}")
        expected_harbor = str(spec.benchmark.settings["harbor_version"])
        try:
            actual_harbor = version("harbor")
        except PackageNotFoundError as error:
            raise StageError("Terminal-Bench requires `uv sync --extra terminalbench`") from error
        if actual_harbor != expected_harbor:
            raise StageError(
                f"Harbor version mismatch: expected {expected_harbor}, got {actual_harbor}"
            )
        if spec.model.subject_agent == "mini-swe-agent":
            try:
                actual_agent = version("mini-swe-agent")
            except PackageNotFoundError as error:
                raise StageError("Terminal-Bench requires mini-swe-agent") from error
            if actual_agent != spec.model.subject_agent_version:
                raise StageError(
                    "mini-swe-agent version mismatch: "
                    f"expected {spec.model.subject_agent_version}, got {actual_agent}"
                )

    def grade(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        del cache_root
        validate_and_summarize(spec, run_dir)

    def normalize(self, spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
        return normalize(spec, run_dir)
