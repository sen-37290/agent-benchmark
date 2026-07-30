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


def _native_usage(trajectory: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    input_tokens = output_tokens = cached_tokens = None
    for message in trajectory.get("messages", []):
        response = message.get("extra", {}).get("response")
        if not isinstance(response, dict):
            continue
        usage = response.get("usage")
        if not isinstance(usage, dict):
            continue
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if cached is None:
            cached = usage.get("cache_read_input_tokens")
        if prompt is not None:
            input_tokens = (input_tokens or 0) + int(prompt)
        if completion is not None:
            output_tokens = (output_tokens or 0) + int(completion)
        if cached is not None:
            cached_tokens = (cached_tokens or 0) + int(cached)
    return input_tokens, output_tokens, cached_tokens


def _native_exit_statuses(directory: Path) -> dict[str, str]:
    import yaml

    reports = sorted(directory.glob("exit_statuses_*.yaml"), key=lambda path: path.stat().st_mtime)
    if not reports:
        return {}
    data = yaml.safe_load(reports[-1].read_text()) or {}
    by_id: dict[str, str] = {}
    for status, ids in (data.get("instances_by_exit_status") or {}).items():
        for task_id in ids or []:
            by_id[str(task_id)] = str(status)
    return by_id


def _normalize_native(
    spec: ResolvedSpec, run_dir: Path, ids: list[str], resolved: set[str]
) -> list[TaskResult]:
    directory = run_dir / "artifacts" / "minisweagent_swebench"
    exit_statuses = _native_exit_statuses(directory)
    results: list[TaskResult] = []
    for task_id in ids:
        trajectory_path = directory / task_id / f"{task_id}.traj.json"
        if not trajectory_path.is_file():
            exit_status = exit_statuses.get(task_id)
            results.append(
                TaskResult(
                    run_id=spec.run_id,
                    task_id=task_id,
                    status="error" if exit_status else "missing",
                    metrics={"resolved": task_id in resolved},
                    error_type=exit_status or "MissingTrajectory",
                    raw_artifacts=[
                        str((directory / "preds.json").relative_to(run_dir))
                    ],
                )
            )
            continue
        trajectory = json.loads(trajectory_path.read_text())
        info = trajectory.get("info", {})
        input_tokens, output_tokens, cached_tokens = _native_usage(trajectory)
        error_type = None
        if info.get("traceback") or info.get("exception_str"):
            error_type = str(info.get("exit_status") or "AgentError")
        cost = info.get("model_stats", {}).get("instance_cost")
        if spec.model.provider == "friendli" and spec.model.byok:
            cost = None
        results.append(
            TaskResult(
                run_id=spec.run_id,
                task_id=task_id,
                status="error" if error_type else "completed",
                metrics={"resolved": task_id in resolved},
                cost_usd=float(cost) if cost is not None else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                error_type=error_type,
                raw_artifacts=[str(trajectory_path.relative_to(run_dir))],
            )
        )
    return results


def normalize(spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
    ids = pool_ids(spec, run_dir)
    valid_ids = set(ids)
    summary_path = run_dir / "artifacts" / "official_summary.json"
    if not summary_path.exists():
        raise StageError("official_summary.json is missing")
    summary = json.loads(summary_path.read_text())
    resolved = set(summary.get("resolved_ids") or summary.get("resolved_instances_ids") or [])

    if spec.benchmark.harness == "mini-swe-agent-native":
        return _normalize_native(spec, run_dir, ids, resolved)

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
