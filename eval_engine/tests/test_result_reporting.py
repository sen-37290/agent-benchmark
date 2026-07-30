import csv
import json
from pathlib import Path

from agent_benchmark.config.loader import resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.run.result import TaskResult, write_report, write_results

ROOT = Path(__file__).parents[1]


def test_reports_reward_when_benchmark_provides_it(tmp_path: Path) -> None:
    pool_file = tmp_path / "pool.json"
    pool_file.write_text(json.dumps({"instance_ids": ["one", "two"]}))
    request = UserRequest(
        benchmark="swebench-verified",
        model="glm-5.2",
        reasoning_effort="xhigh",
        provider="openrouter",
        workers=1,
        budget_usd=10,
    )
    spec = resolve(request, "test-reward-report", ROOT, pool_file)
    results = [
        TaskResult(
            run_id=spec.run_id,
            task_id="one",
            status="completed",
            metrics={"reward": 1.0, "resolved": True},
        ),
        TaskResult(
            run_id=spec.run_id,
            task_id="two",
            status="error",
            metrics={"reward": None, "resolved": False},
        ),
    ]

    write_results(tmp_path, results)
    summary = write_report(spec, tmp_path, results)

    assert summary["mean_reward"] == 0.5
    assert summary["accuracy"] == 0.5
    with (tmp_path / "results" / "summary.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["reward"] == "1.0"
    assert json.loads((tmp_path / "report" / "summary.json").read_text()) == summary


def test_reports_aider_attempt_rates_and_missing_denominators(tmp_path: Path) -> None:
    pool_file = tmp_path / "pool.json"
    pool_file.write_text(json.dumps({"instance_ids": ["python/one", "rust/two"]}))
    request = UserRequest(
        benchmark="aider-polyglot",
        model="glm-5.2",
        reasoning_effort="xhigh",
        provider="openrouter",
        workers=1,
        budget_usd=10,
    )
    spec = resolve(request, "test-aider-report", ROOT, pool_file)
    results = [
        TaskResult(
            run_id=spec.run_id,
            task_id=task_id,
            status="missing",
            metrics={"pass_at_1": False, "pass_at_2": False, "resolved": False},
        )
        for task_id in ("python/one", "rust/two")
    ]

    summary = write_report(spec, tmp_path, results)

    assert summary["official_pass_rate_1"] is None
    assert summary["official_pass_rate_2"] is None
    assert summary["pool_pass_rate_1"] == 0
    assert summary["pool_pass_rate_2"] == 0
    assert summary["officially_comparable"] is False
    report = (tmp_path / "report" / "report.md").read_text()
    assert "Aider pass rate 1 (completed denominator): n/a" in report
    assert "Pool pass rate 2: 0.0%" in report
