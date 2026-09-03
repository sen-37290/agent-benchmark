from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.result import TaskResult
from agent_benchmark.run.retry import attempt_costs, attempt_exhausted
from agent_benchmark.run.stop import stop_reason


def pool_ids(spec: ResolvedSpec, run_dir: Path) -> list[str]:
    payload = json.loads((run_dir / spec.benchmark.pool_path).read_text())
    return [str(task_id) for task_id in payload["instance_ids"]]


def trial_results(jobs_dir: Path) -> list[Path]:
    return sorted(path for path in jobs_dir.glob("*/*/result.json") if path.parent != jobs_dir)


def task_id(result: dict[str, object]) -> str | None:
    name = result.get("task_name")
    if not isinstance(name, str):
        return None
    return name.rsplit("/", 1)[-1]


def _duration(result: dict[str, object]) -> float | None:
    started = result.get("started_at")
    finished = result.get("finished_at")
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    return (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _terminal_reasoning_audit(trajectory: dict[str, object]) -> dict[str, object]:
    raw_steps = trajectory.get("steps")
    steps = raw_steps if isinstance(raw_steps, list) else []
    agent_steps = [
        step for step in steps if isinstance(step, dict) and step.get("source") == "agent"
    ]
    models = sorted(
        {str(step["model_name"]) for step in agent_steps if isinstance(step.get("model_name"), str)}
    )
    return {
        "assistant_responses": len(agent_steps),
        "reasoning_responses": sum(bool(step.get("reasoning_content")) for step in agent_steps),
        "reasoning_details_responses": 0,
        "models": models,
        "providers": [],
        "provider_metadata_available": False,
    }


def normalize(spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
    expected = pool_ids(spec, run_dir)
    by_id: dict[str, TaskResult] = {}
    audits: dict[str, dict[str, object]] = {}
    for result_path in trial_results(run_dir / "artifacts" / "harbor_jobs"):
        raw = json.loads(result_path.read_text())
        identity = task_id(raw)
        if identity not in expected:
            continue
        trajectory_path = result_path.parent / "agent" / "trajectory.json"
        if trajectory_path.is_file():
            audits[identity] = _terminal_reasoning_audit(json.loads(trajectory_path.read_text()))
        exception = raw.get("exception_info")
        verifier = raw.get("verifier_result")
        rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
        reward = _number(rewards.get("reward")) if isinstance(rewards, dict) else None
        agent = raw.get("agent_result")
        agent = agent if isinstance(agent, dict) else {}
        error_type = exception.get("exception_type") if isinstance(exception, dict) else None
        normalized_reward = 0.0 if exception else reward
        attempt_count, manifest_final, overhead, cost_complete = attempt_costs(run_dir, identity)
        raw_final = _number(agent.get("cost_usd"))
        final_cost = manifest_final if manifest_final is not None else raw_final
        by_id[identity] = TaskResult(
            run_id=spec.run_id,
            task_id=identity,
            status="error" if exception else "completed",
            metrics={
                "reward": normalized_reward,
                "resolved": not exception and reward is not None and reward > 0,
            },
            cost_usd=final_cost,
            attempt_count=attempt_count,
            retry_overhead_cost_usd=overhead,
            billed_cost_usd=(final_cost + overhead) if final_cost is not None else None,
            retry_cost_complete=cost_complete,
            retry_exhausted=attempt_exhausted(run_dir, identity),
            input_tokens=agent.get("n_input_tokens"),
            output_tokens=agent.get("n_output_tokens"),
            cached_tokens=agent.get("n_cache_tokens"),
            duration_seconds=_duration(raw),
            error_type=str(error_type) if error_type else None,
            raw_artifacts=[str(result_path.relative_to(run_dir))],
        )
    # A task with no result is a defect *unless* the run was deliberately stopped before it
    # started, in which case it never ran and must not be scored as a failure.
    stopped = stop_reason(run_dir)
    absent_status = "unrun" if stopped else "missing"
    absent_error = None if stopped else "MissingTrialResult"
    results = [
        by_id.get(task_id)
        or TaskResult(
            run_id=spec.run_id,
            task_id=task_id,
            status=absent_status,
            metrics={"reward": None, "resolved": False},
            error_type=absent_error,
        )
        for task_id in expected
    ]
    (run_dir / "artifacts" / "reasoning_audit.json").write_text(
        json.dumps({"tasks": audits}, indent=2, sort_keys=True) + "\n"
    )
    return results


def validate_and_summarize(spec: ResolvedSpec, run_dir: Path) -> dict[str, object]:
    expected = set(pool_ids(spec, run_dir))
    seen: set[str] = set()
    rewards: list[float] = []
    errors: list[str] = []
    for result_path in trial_results(run_dir / "artifacts" / "harbor_jobs"):
        raw = json.loads(result_path.read_text())
        identity = task_id(raw)
        if identity not in expected:
            continue
        if identity in seen:
            raise StageError(f"duplicate Terminal-Bench result for {identity}")
        seen.add(identity)
        exception = raw.get("exception_info")
        verifier = raw.get("verifier_result")
        reward_map = verifier.get("rewards") if isinstance(verifier, dict) else None
        reward = _number(reward_map.get("reward")) if isinstance(reward_map, dict) else None
        if exception:
            errors.append(identity)
        elif reward is None:
            raise StageError(f"Terminal-Bench result has no verifier reward: {identity}")
        else:
            rewards.append(reward)
    unrun: set[str] = set()
    if seen != expected:
        reason = stop_reason(run_dir)
        if reason is None:
            missing = ", ".join(sorted(expected - seen)[:3])
            raise StageError(f"Terminal-Bench results are incomplete; missing: {missing}")
        # Stopped on purpose (cost cap or operator). Summarise what ran and name the rest.
        unrun = expected - seen
    # Rates are over the tasks that actually ran, so a partial run is not reported as if the
    # unrun tasks had all scored zero. task_count keeps the pool size for context.
    scored = len(seen) or 1
    summary = {
        "task_count": len(expected),
        "attempted_count": len(seen),
        "unrun_count": len(unrun),
        "unrun_task_ids": sorted(unrun),
        "stop_reason": stop_reason(run_dir),
        "successful_count": sum(reward > 0 for reward in rewards),
        "error_count": len(errors),
        "mean_reward": sum(rewards) / scored,
        "accuracy": sum(reward > 0 for reward in rewards) / scored,
    }
    (run_dir / "artifacts" / "terminal_bench_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    return summary
