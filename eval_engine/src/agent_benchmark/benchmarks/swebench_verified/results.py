from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.result import TaskResult

INSTANCE_RE = re.compile(r"[A-Za-z0-9][\w.-]*__[\w.-]+-\d+")


def pool_ids(spec: ResolvedSpec, run_dir: Path) -> list[str]:
    return json.loads((run_dir / spec.benchmark.pool_path).read_text())["instance_ids"]


def trial_results(job_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(job_dir.glob("**/result.json"))
        if (path.parent / "agent").is_dir() or (path.parent / "verifier").is_dir()
    ]


def instance_id(trial_dir: Path, result: dict[str, Any], valid_ids: set[str]) -> str | None:
    probes = [result.get("task_name"), result.get("trial_name"), str(trial_dir)]
    task_id = result.get("task_id") or {}
    if isinstance(task_id, dict):
        probes.insert(0, task_id.get("path"))
    for probe in probes:
        match = INSTANCE_RE.search(str(probe or ""))
        if match and match.group(0) in valid_ids:
            return match.group(0)
    return None


def cost_and_tokens(
    result: dict[str, Any],
) -> tuple[float | None, int | None, int | None, int | None]:
    contexts: list[dict[str, Any]] = []
    if result.get("agent_result"):
        contexts.append(result["agent_result"])
    else:
        contexts.extend(
            step["agent_result"]
            for step in (result.get("step_results") or [])
            if step.get("agent_result")
        )
    cost: float | None = None
    input_tokens = output_tokens = cached_tokens = None
    for context in contexts:
        if context.get("cost_usd") is not None:
            cost = (cost or 0.0) + float(context["cost_usd"])
        if context.get("n_input_tokens") is not None:
            input_tokens = (input_tokens or 0) + int(context["n_input_tokens"])
        if context.get("n_output_tokens") is not None:
            output_tokens = (output_tokens or 0) + int(context["n_output_tokens"])
        if context.get("n_cache_tokens") is not None:
            cached_tokens = (cached_tokens or 0) + int(context["n_cache_tokens"])
    return cost, input_tokens, output_tokens, cached_tokens


def duration(result: dict[str, Any]) -> float | None:
    try:
        return (
            datetime.fromisoformat(result["finished_at"])
            - datetime.fromisoformat(result["started_at"])
        ).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


def normalize(spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
    ids = pool_ids(spec, run_dir)
    valid_ids = set(ids)
    summary_path = run_dir / "artifacts" / "official_summary.json"
    if not summary_path.exists():
        raise StageError("official_summary.json is missing")
    summary = json.loads(summary_path.read_text())
    resolved = set(summary.get("resolved_ids") or summary.get("resolved_instances_ids") or [])

    by_id: dict[str, TaskResult] = {}
    jobs = run_dir / "artifacts" / "harbor_jobs"
    for result_path in trial_results(jobs):
        result = json.loads(result_path.read_text())
        task_id = instance_id(result_path.parent, result, valid_ids)
        if not task_id:
            continue
        cost, input_tokens, output_tokens, cached_tokens = cost_and_tokens(result)
        exception = result.get("exception_info") or {}
        error_type = exception.get("exception_type")
        by_id[task_id] = TaskResult(
            run_id=spec.run_id,
            task_id=task_id,
            status="error" if error_type else "completed",
            metrics={"resolved": task_id in resolved},
            cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            duration_seconds=duration(result),
            error_type=error_type,
            raw_artifacts=[str(result_path.relative_to(run_dir))],
        )
    return [
        by_id.get(task_id)
        or TaskResult(
            run_id=spec.run_id,
            task_id=task_id,
            status="missing",
            metrics={"resolved": False},
            error_type="MissingTrial",
        )
        for task_id in ids
    ]
