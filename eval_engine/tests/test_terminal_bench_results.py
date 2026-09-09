import json
from pathlib import Path

import pytest

from agent_benchmark.benchmarks.terminal_bench.pool import create_pool
from agent_benchmark.benchmarks.terminal_bench.results import normalize, validate_and_summarize
from agent_benchmark.config.loader import resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import StageError

ROOT = Path(__file__).parents[1]


def setup_run(tmp_path: Path):
    pool = tmp_path / "pool-source.json"
    create_pool(pool, "random", 3)
    request = UserRequest(
        benchmark="terminal-bench-2.1",
        sampling="random",
        size=3,
        model="glm-5.2",
        reasoning_effort="xhigh",
        provider="openrouter",
        workers=1,
        budget_usd=10,
    )
    spec = resolve(request, "test-terminal-results", ROOT, pool)
    run_dir = tmp_path / "run"
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "artifacts" / "harbor_jobs" / spec.run_id).mkdir(parents=True)
    (run_dir / spec.benchmark.pool_path).write_text(pool.read_text())
    ids = json.loads(pool.read_text())["instance_ids"]
    return spec, run_dir, ids


def write_trial(
    run_dir: Path,
    job_name: str,
    task_id: str,
    *,
    reward: float | None,
    exception: str | None = None,
) -> None:
    trial = run_dir / "artifacts" / "harbor_jobs" / job_name / f"{task_id}__trial"
    trial.mkdir()
    payload = {
        "task_name": f"terminal-bench/{task_id}",
        "started_at": "2026-07-29T00:00:00+00:00",
        "finished_at": "2026-07-29T00:01:30+00:00",
        "agent_result": {
            "n_input_tokens": 100,
            "n_cache_tokens": 20,
            "n_output_tokens": 10,
            "cost_usd": 0.25,
        },
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
        "exception_info": (
            {"exception_type": exception, "exception_message": "failed"} if exception else None
        ),
    }
    (trial / "result.json").write_text(json.dumps(payload))


def test_normalizes_reward_error_cost_tokens_and_missing(tmp_path: Path) -> None:
    spec, run_dir, ids = setup_run(tmp_path)
    write_trial(run_dir, spec.run_id, ids[0], reward=1.0)
    write_trial(run_dir, spec.run_id, ids[1], reward=None, exception="AgentTimeoutError")

    results = normalize(spec, run_dir)
    assert [result.status for result in results] == ["completed", "error", "missing"]
    assert results[0].metrics == {"reward": 1.0, "resolved": True}
    assert results[0].cost_usd == 0.25
    assert results[0].duration_seconds == 90
    assert results[1].error_type == "AgentTimeoutError"
    assert results[1].metrics == {"reward": 0.0, "resolved": False}
    assert results[2].metrics == {"reward": None, "resolved": False}


def test_grade_only_validates_inline_rewards(tmp_path: Path) -> None:
    spec, run_dir, ids = setup_run(tmp_path)
    write_trial(run_dir, spec.run_id, ids[0], reward=1.0)
    write_trial(run_dir, spec.run_id, ids[1], reward=0.0)
    write_trial(run_dir, spec.run_id, ids[2], reward=None, exception="VerifierTimeoutError")

    summary = validate_and_summarize(spec, run_dir)
    assert summary == {
        "task_count": 3,
        "successful_count": 1,
        "error_count": 1,
        "mean_reward": pytest.approx(1 / 3),
        "accuracy": pytest.approx(1 / 3),
    }
    assert (run_dir / "artifacts" / "terminal_bench_summary.json").is_file()


def test_grade_rejects_non_error_without_reward(tmp_path: Path) -> None:
    spec, run_dir, ids = setup_run(tmp_path)
    for task_id in ids:
        write_trial(run_dir, spec.run_id, task_id, reward=1.0)
    result = next((run_dir / "artifacts" / "harbor_jobs").glob("*/*/result.json"))
    payload = json.loads(result.read_text())
    payload["verifier_result"] = None
    result.write_text(json.dumps(payload))

    with pytest.raises(StageError, match="no verifier reward"):
        validate_and_summarize(spec, run_dir)
