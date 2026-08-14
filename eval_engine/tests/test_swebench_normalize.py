import json
from pathlib import Path

import pytest

from agent_benchmark.benchmarks.swebench_verified import SwebenchVerified
from agent_benchmark.benchmarks.swebench_verified.results import cost_and_tokens
from agent_benchmark.config.loader import resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.harnesses.mini_swe_agent_native import (
    build_command,
    collected_cost,
    task_failures,
)

PROJECT = Path(__file__).parents[1]


def test_nullable_step_results_have_no_usage() -> None:
    assert cost_and_tokens({"agent_result": None, "step_results": None}) == (
        None,
        None,
        None,
        None,
    )


def test_normalizes_official_grade_and_missing_trial(tmp_path: Path) -> None:
    pool_file = tmp_path / "pool.json"
    pool_file.write_text(
        json.dumps(
            {"instance_ids": ["django__django-13741", "pytest-dev__pytest-7571"]}
        )
    )
    request = UserRequest(
        benchmark="swebench-verified-harbor",
        model="glm-5.2",
        reasoning_effort="xhigh",
        provider="openrouter",
        workers=2,
        budget_usd=10,
    )
    spec = resolve(request, "test-run-789", PROJECT, pool_file)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "pool.json").write_bytes(pool_file.read_bytes())
    trial = tmp_path / "artifacts" / "harbor_jobs" / "timestamp" / "django__django-13741-trial"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "swe-bench/swebench-verified__django__django-13741",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:01:00+00:00",
                "agent_result": {"cost_usd": 1.25, "n_input_tokens": 100},
            }
        )
    )
    (tmp_path / "artifacts" / "official_summary.json").write_text(
        json.dumps({"resolved_ids": ["django__django-13741"]})
    )

    results = SwebenchVerified().normalize(spec, tmp_path)
    assert len(results) == 2
    assert results[0].metrics["resolved"] is True
    assert results[0].cost_usd == 1.25
    assert results[1].status == "missing"


def native_spec(tmp_path: Path, *, per_task_cost_limit_usd: float | None = None):
    pool = tmp_path / "pool.json"
    pool.write_text(
        json.dumps(
            {"instance_ids": ["django__django-13741", "pytest-dev__pytest-7571"]}
        )
    )
    spec = resolve(
        UserRequest(
            benchmark="swebench-verified",
            model="glm-5.2",
            reasoning_effort="xhigh",
            provider="openrouter",
            workers=2,
            budget_usd=10,
            per_task_cost_limit_usd=per_task_cost_limit_usd,
        ),
        "test-native-swebench",
        PROJECT,
        pool,
    )
    run_dir = tmp_path / "run"
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / spec.benchmark.pool_path).write_text(pool.read_text())
    return spec, run_dir


def test_official_profile_uses_native_runner_and_fixed_budget(tmp_path: Path) -> None:
    spec, run_dir = native_spec(tmp_path)

    assert spec.benchmark.harness == "mini-swe-agent-native"
    assert spec.model.subject_agent == "mini-swe-agent"
    assert spec.budget.per_task_usd == 3.0
    command = build_command(spec, run_dir)
    assert command[:4] == ["uv", "run", "mini-extra", "swebench"]
    assert command[command.index("--subset") + 1] == "princeton-nlp/SWE-bench_Verified"
    assert command[command.index("--split") + 1] == "test"
    assert command[command.index("--workers") + 1] == "2"
    assert "swebench.yaml" in command
    filter_spec = command[command.index("--filter") + 1]
    assert filter_spec.startswith("^(?:") and filter_spec.endswith(")$")
    assert r"django__django\-13741" in filter_spec
    assert r"pytest\-dev__pytest\-7571" in filter_spec


def test_official_prepare_validates_packaged_config_without_harbor(tmp_path: Path) -> None:
    spec, run_dir = native_spec(tmp_path)
    cache_root = tmp_path / "cache"

    SwebenchVerified().prepare(spec, run_dir, cache_root)

    assert not cache_root.exists()


def test_official_profile_rejects_nonstandard_per_task_budget(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="requires --per-task-cost-limit-usd 3"):
        native_spec(tmp_path, per_task_cost_limit_usd=5)


def test_native_cost_reader_sums_completed_trajectories(tmp_path: Path) -> None:
    for task_id, cost in (("one", 1.25), ("two", 2.5)):
        path = tmp_path / task_id / f"{task_id}.traj.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"info": {"model_stats": {"instance_cost": cost}}}))
    assert collected_cost(tmp_path) == 3.75


def test_native_task_failures_reject_runtime_errors_but_allow_limit_exits(
    tmp_path: Path,
) -> None:
    for task_id, exit_status in (
        ("submitted", "Submitted"),
        ("limited", "LimitsExceeded"),
        ("broken", "RuntimeError"),
    ):
        path = tmp_path / task_id / f"{task_id}.traj.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"info": {"exit_status": exit_status}}))

    assert task_failures(tmp_path, ["submitted", "limited", "broken", "missing"]) == {
        "broken": "RuntimeError",
        "missing": "MissingTrajectory",
    }


def test_native_normalization_uses_trajectory_and_official_summary(tmp_path: Path) -> None:
    spec, run_dir = native_spec(tmp_path)
    output = run_dir / "artifacts" / "minisweagent_swebench"
    task_id = "django__django-13741"
    trajectory = output / task_id / f"{task_id}.traj.json"
    trajectory.parent.mkdir(parents=True)
    trajectory.write_text(
        json.dumps(
            {
                "info": {
                    "exit_status": "Submitted",
                    "model_stats": {"instance_cost": 1.5, "api_calls": 2},
                },
                "messages": [
                    {
                        "extra": {
                            "response": {
                                "usage": {
                                    "prompt_tokens": 10,
                                    "completion_tokens": 4,
                                    "prompt_tokens_details": {"cached_tokens": 3},
                                }
                            }
                        }
                    }
                ],
            }
        )
    )
    (output / "preds.json").write_text("{}")
    (run_dir / "artifacts" / "official_summary.json").write_text(
        json.dumps({"resolved_ids": [task_id]})
    )

    results = SwebenchVerified().normalize(spec, run_dir)

    assert results[0].status == "completed"
    assert results[0].metrics == {"resolved": True}
    assert results[0].cost_usd == 1.5
    assert (results[0].input_tokens, results[0].output_tokens, results[0].cached_tokens) == (
        10,
        4,
        3,
    )
    assert results[1].status == "missing"


def test_native_grade_passes_official_predictions_through_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, run_dir = native_spec(tmp_path)
    predictions_path = run_dir / "artifacts" / "minisweagent_swebench" / "preds.json"
    predictions_path.parent.mkdir(parents=True)
    original = json.dumps(
        {
            "django__django-13741": {
                "instance_id": "django__django-13741",
                "model_name_or_path": "z-ai/glm-5.2",
                "model_patch": "diff --git a/a.py b/a.py\n",
            },
            "pytest-dev__pytest-7571": {
                "instance_id": "pytest-dev__pytest-7571",
                "model_name_or_path": "z-ai/glm-5.2",
                "model_patch": "",
            },
        },
        indent=2,
    )
    predictions_path.write_text(original)
    commands: list[list[str]] = []

    def fake_run_logged(command, *, cwd, **kwargs):
        del kwargs
        commands.append(list(command))
        (cwd / f"summary.agent_bench_{spec.run_id}.json").write_text(
            json.dumps({"resolved_ids": []})
        )

    monkeypatch.setattr(
        "agent_benchmark.benchmarks.swebench_verified.benchmark.version",
        lambda _: "4.0.3",
    )
    monkeypatch.setattr(
        "agent_benchmark.benchmarks.swebench_verified.benchmark.run_logged",
        fake_run_logged,
    )

    SwebenchVerified().grade(spec, run_dir, tmp_path / "cache")

    command = commands[0]
    assert command[command.index("--predictions_path") + 1] == str(predictions_path)
    assert predictions_path.read_text() == original
    assert (run_dir / "artifacts" / "official_summary.json").is_file()
