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
    attempt_count: int = 1
    retry_overhead_cost_usd: float | None = None
    billed_cost_usd: float | None = None
    retry_cost_complete: bool = True
    retry_exhausted: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cache_creation_tokens: int | None = None
    llm_requests: int | None = None
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
        "attempt_count",
        "retry_overhead_cost_usd",
        "billed_cost_usd",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "cache_creation_tokens",
        "llm_requests",
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
                    "attempt_count": result.attempt_count,
                    "retry_overhead_cost_usd": result.retry_overhead_cost_usd,
                    "billed_cost_usd": result.billed_cost_usd,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cached_tokens": result.cached_tokens,
                    "cache_creation_tokens": result.cache_creation_tokens,
                    "llm_requests": result.llm_requests,
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
    retry_costs = [
        result.retry_overhead_cost_usd
        for result in results
        if result.retry_overhead_cost_usd is not None
    ]
    billed_costs = [
        result.billed_cost_usd for result in results if result.billed_cost_usd is not None
    ]
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
        "measured_retry_overhead_cost_usd": sum(retry_costs) if retry_costs else 0.0,
        "measured_billed_cost_usd": sum(billed_costs) if billed_costs else None,
        "retried_task_count": sum(result.attempt_count > 1 for result in results),
        "retry_attempt_count": sum(max(0, result.attempt_count - 1) for result in results),
        "retry_cost_complete": all(result.retry_cost_complete for result in results),
        "exhausted_retry_count": sum(result.retry_exhausted for result in results),
    }
    for field, key in (
        ("input_tokens", "avg_input_tokens"),
        ("output_tokens", "avg_output_tokens"),
        ("cached_tokens", "avg_cache_read_tokens"),
        ("cache_creation_tokens", "avg_cache_creation_tokens"),
        ("llm_requests", "avg_llm_requests"),
    ):
        values = [
            getattr(result, field) for result in results if getattr(result, field) is not None
        ]
        summary[key] = sum(values) / len(values) if values else None
    if has_reward_metric:
        summary["mean_reward"] = sum(rewards) / len(results)
        summary["accuracy"] = len(resolved) / len(results) if results else None
    if any("pass_at_1" in result.metrics for result in results):
        completed_results = [result for result in results if result.status != "missing"]
        pass_1 = sum(result.metrics.get("pass_at_1") is True for result in results)
        pass_2 = sum(result.metrics.get("pass_at_2") is True for result in results)
        completed_count = len(completed_results)
        summary.update(
            {
                "official_pass_rate_1": pass_1 / completed_count if completed_count else None,
                "official_pass_rate_2": pass_2 / completed_count if completed_count else None,
                "pool_pass_rate_1": pass_1 / len(results) if results else None,
                "pool_pass_rate_2": pass_2 / len(results) if results else None,
                "officially_comparable": (
                    spec.benchmark.profile == "aider-polyglot"
                    and len(results) == 225
                    and completed_count == 225
                    and spec.model.subject_agent == "aider"
                    and spec.benchmark.harness == "aider-native"
                ),
            }
        )
    reasoning_audit_path = run_dir / "artifacts" / "reasoning_audit.json"
    if reasoning_audit_path.is_file():
        reasoning_audit = json.loads(reasoning_audit_path.read_text())
        audits = list(reasoning_audit.get("tasks", {}).values())
        summary["assistant_response_count"] = sum(
            int(audit.get("assistant_responses", 0)) for audit in audits
        )
        summary["reasoning_response_count"] = sum(
            int(audit.get("reasoning_responses", 0)) for audit in audits
        )
        summary["reasoning_details_response_count"] = sum(
            int(audit.get("reasoning_details_responses", 0)) for audit in audits
        )
        summary["returned_models"] = sorted(
            {model for audit in audits for model in audit.get("models", [])}
        )
        summary["returned_providers"] = sorted(
            {provider for audit in audits for provider in audit.get("providers", [])}
        )
    report_dir = run_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    rate = summary["resolve_rate"]
    rate_text = "n/a" if rate is None else f"{rate:.1%}"
    cost = summary["measured_total_cost_usd"]
    cost_text = "unavailable" if cost is None else f"${cost:.2f}"
    overhead = summary["measured_retry_overhead_cost_usd"]
    billed = summary["measured_billed_cost_usd"]
    overhead_text = f"${overhead:.2f}"
    billed_text = "unavailable" if billed is None else f"${billed:.2f}"
    reward_line = (
        f"- Mean reward: {summary['mean_reward']:.4f}\n" if "mean_reward" in summary else ""
    )
    aider_lines = ""
    if "official_pass_rate_2" in summary:
        official_1 = summary["official_pass_rate_1"]
        official_2 = summary["official_pass_rate_2"]
        pool_1 = summary["pool_pass_rate_1"]
        pool_2 = summary["pool_pass_rate_2"]
        comparable = summary["officially_comparable"]
        official_1_text = "n/a" if official_1 is None else f"{official_1:.1%}"
        official_2_text = "n/a" if official_2 is None else f"{official_2:.1%}"
        pool_1_text = "n/a" if pool_1 is None else f"{pool_1:.1%}"
        pool_2_text = "n/a" if pool_2 is None else f"{pool_2:.1%}"
        aider_lines = (
            f"- Aider pass rate 1 (completed denominator): {official_1_text}\n"
            f"- Aider pass rate 2 (completed denominator): {official_2_text}\n"
            f"- Pool pass rate 1: {pool_1_text}\n"
            f"- Pool pass rate 2: {pool_2_text}\n"
            f"- Officially comparable: {str(comparable).lower()}\n"
        )
    reasoning_lines = ""
    if "assistant_response_count" in summary:
        reasoning_lines = (
            f"- Assistant responses: {summary['assistant_response_count']}\n"
            f"- Responses with reasoning: {summary['reasoning_response_count']}\n"
            "- Responses with reasoning details: "
            f"{summary['reasoning_details_response_count']}\n"
            f"- Returned models: {summary['returned_models']}\n"
            f"- Returned providers: {summary['returned_providers']}\n"
        )
    (report_dir / "report.md").write_text(
        f"# Evaluation report: {spec.run_id}\n\n"
        f"- Benchmark: `{spec.benchmark.profile}` "
        f"({spec.benchmark.sampling}, n={spec.benchmark.sample_size})\n"
        f"- Model: `{spec.model.profile}` "
        f"({spec.model.reasoning_effort or 'provider default'})\n"
        f"- Official resolved: {len(resolved)}/{len(results)} ({rate_text})\n"
        f"- Completed: {len(completed)}/{len(results)}\n"
        f"{reward_line}"
        f"{aider_lines}"
        f"{reasoning_lines}"
        f"- Measured generation cost: {cost_text}\n"
        f"- Average LLM requests: {summary['avg_llm_requests'] or 'n/a'}\n"
        f"- Measured retry overhead: {overhead_text}\n"
        f"- Measured billed cost (all attempts): {billed_text}\n"
        f"- Retried tasks: {summary['retried_task_count']}\n"
    )
    return summary
