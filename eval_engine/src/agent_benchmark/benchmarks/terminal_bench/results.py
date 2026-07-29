from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.result import TaskResult


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


def normalize(spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
    expected = pool_ids(spec, run_dir)
    by_id: dict[str, TaskResult] = {}
    for result_path in trial_results(run_dir / "artifacts" / "harbor_jobs"):
        raw = json.loads(result_path.read_text())
        identity = task_id(raw)
        if identity not in expected:
            continue
        exception = raw.get("exception_info")
        verifier = raw.get("verifier_result")
        rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
        reward = _number(rewards.get("reward")) if isinstance(rewards, dict) else None
        agent = raw.get("agent_result")
        agent = agent if isinstance(agent, dict) else {}
        error_type = exception.get("exception_type") if isinstance(exception, dict) else None
        normalized_reward = 0.0 if exception else reward
        by_id[identity] = TaskResult(
            run_id=spec.run_id,
            task_id=identity,
            status="error" if exception else "completed",
            metrics={
                "reward": normalized_reward,
                "resolved": not exception and reward is not None and reward > 0,
            },
            cost_usd=_number(agent.get("cost_usd")),
            input_tokens=agent.get("n_input_tokens"),
            output_tokens=agent.get("n_output_tokens"),
            cached_tokens=agent.get("n_cache_tokens"),
            duration_seconds=_duration(raw),
            error_type=str(error_type) if error_type else None,
            raw_artifacts=[str(result_path.relative_to(run_dir))],
        )
    return [
        by_id.get(task_id)
        or TaskResult(
            run_id=spec.run_id,
            task_id=task_id,
            status="missing",
            metrics={"reward": None, "resolved": False},
            error_type="MissingTrialResult",
        )
        for task_id in expected
    ]


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
    if seen != expected:
        missing = ", ".join(sorted(expected - seen)[:3])
        raise StageError(f"Terminal-Bench results are incomplete; missing: {missing}")
    summary = {
        "task_count": len(expected),
        "successful_count": sum(reward > 0 for reward in rewards),
        "error_count": len(errors),
        "mean_reward": sum(rewards) / len(expected),
        "accuracy": sum(reward > 0 for reward in rewards) / len(expected),
    }
    (run_dir / "artifacts" / "terminal_bench_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    return summary
