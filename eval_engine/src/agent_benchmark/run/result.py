from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agent_benchmark.config.schema import ResolvedSpec


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


def write_results(run_dir: Path, results: list[TaskResult]) -> None:
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    with (results_dir / "task_results.jsonl").open("w") as stream:
        for result in results:
            stream.write(result.model_dump_json() + "\n")

    columns = [
        "run_id",
        "task_id",
        "status",
        "resolved",
        "reward",
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "duration_seconds",
        "error_type",
    ]
    with (results_dir / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "run_id": result.run_id,
                    "task_id": result.task_id,
                    "status": result.status,
                    "resolved": result.metrics.get("resolved"),
                    "reward": result.metrics.get("reward"),
                    "cost_usd": result.cost_usd,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cached_tokens": result.cached_tokens,
                    "duration_seconds": result.duration_seconds,
                    "error_type": result.error_type,
                }
            )


def read_results(run_dir: Path) -> list[TaskResult]:
    path = run_dir / "results" / "task_results.jsonl"
    return [TaskResult.model_validate_json(line) for line in path.read_text().splitlines() if line]


def write_report(spec: ResolvedSpec, run_dir: Path, results: list[TaskResult]) -> dict[str, object]:
    completed = [result for result in results if result.status == "completed"]
    resolved = [result for result in results if result.metrics.get("resolved") is True]
    measured_costs = [result.cost_usd for result in results if result.cost_usd is not None]
    rewards = [
        float(result.metrics["reward"])
        for result in results
        if isinstance(result.metrics.get("reward"), int | float)
        and not isinstance(result.metrics.get("reward"), bool)
    ]
    has_reward_metric = any("reward" in result.metrics for result in results)
    summary: dict[str, object] = {
        "run_id": spec.run_id,
        "benchmark": spec.benchmark.profile,
        "sampling": spec.benchmark.sampling,
        "sample_size": spec.benchmark.sample_size,
        "model": spec.model.profile,
        "reasoning_effort": spec.model.reasoning_effort,
        "task_count": len(results),
        "completed_count": len(completed),
        "resolved_count": len(resolved),
        "resolve_rate": len(resolved) / len(results) if results else None,
        "error_count": sum(result.status == "error" for result in results),
        "missing_count": sum(result.status == "missing" for result in results),
        "measured_total_cost_usd": sum(measured_costs) if measured_costs else None,
    }
    if has_reward_metric:
        summary["mean_reward"] = sum(rewards) / len(results)
        summary["accuracy"] = len(resolved) / len(results) if results else None
    report_dir = run_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    rate = summary["resolve_rate"]
    rate_text = "n/a" if rate is None else f"{rate:.1%}"
    cost = summary["measured_total_cost_usd"]
    cost_text = "unavailable" if cost is None else f"${cost:.2f}"
    reward_line = (
        f"- Mean reward: {summary['mean_reward']:.4f}\n" if "mean_reward" in summary else ""
    )
    (report_dir / "report.md").write_text(
        f"# Evaluation report: {spec.run_id}\n\n"
        f"- Benchmark: `{spec.benchmark.profile}` "
        f"({spec.benchmark.sampling}, n={spec.benchmark.sample_size})\n"
        f"- Model: `{spec.model.profile}` ({spec.model.reasoning_effort})\n"
        f"- Official resolved: {len(resolved)}/{len(results)} ({rate_text})\n"
        f"- Completed: {len(completed)}/{len(results)}\n"
        f"{reward_line}"
        f"- Measured generation cost: {cost_text}\n"
    )
    return summary
