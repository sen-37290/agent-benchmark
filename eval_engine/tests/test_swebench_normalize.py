import json
from pathlib import Path

import pytest
import yaml

from agent_benchmark.benchmarks.swebench_verified import SwebenchVerified
from agent_benchmark.benchmarks.swebench_verified import benchmark as swebench_benchmark
from agent_benchmark.benchmarks.swebench_verified import images as swebench_images
from agent_benchmark.benchmarks.swebench_verified.pool import _pinned_ids
from agent_benchmark.benchmarks.swebench_verified.results import cost_and_tokens
from agent_benchmark.config.loader import resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.harnesses.mini_swe_agent_native import (
    PULL_TIMEOUT_SECONDS,
    build_command,
    collected_cost,
    harness_config_path,
    task_failures,
    write_harness_config,
)

PROJECT = Path(__file__).parents[1]


def test_nullable_step_results_have_no_usage() -> None:
    assert cost_and_tokens({"agent_result": None, "step_results": None}) == (
        None,
        None,
        None,
        None,
    )


def test_normalizes_official_grade_and_missing_trial(tmp_path: Path, pool_file: Path) -> None:
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


def native_spec(
    tmp_path: Path,
    *,
    per_task_cost_limit_usd: float | None = None,
    allow_cost_limit_override: bool = False,
    anthropic_fallbacks: str | None = None,
):
    pool = tmp_path / "pool.json"
    pool.write_text(
        json.dumps({"instance_ids": ["django__django-13741", "pytest-dev__pytest-7571"]})
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
            allow_cost_limit_override=allow_cost_limit_override,
            anthropic_fallbacks=anthropic_fallbacks,
        ),
        "test-native-swebench",
        PROJECT,
        pool,
    )
    run_dir = tmp_path / "run"
    # exist_ok: a test may build two specs (with and without a flag) under one tmp_path.
    (run_dir / "inputs").mkdir(parents=True, exist_ok=True)
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


def test_official_prepare_validates_packaged_config_without_harbor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, run_dir = native_spec(tmp_path)
    cache_root = tmp_path / "cache"
    monkeypatch.setattr(swebench_benchmark, "prepull", lambda *a, **k: [])

    SwebenchVerified().prepare(spec, run_dir, cache_root)

    assert not cache_root.exists()


def test_official_prepare_prewarms_every_pool_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of prepare for this harness: no task should ever pull its own image.

    mini-swe-agent pulls implicitly from `docker run` under a 120s wall clock, and a wave of
    concurrent first-touch pulls is what silently lost 18 matplotlib tasks per model.
    """
    spec, run_dir = native_spec(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(
        swebench_benchmark,
        "prepull",
        lambda ids, log_path, **kwargs: seen.update(ids=list(ids), kwargs=kwargs) or [],
    )

    SwebenchVerified().prepare(spec, run_dir, tmp_path / "cache")

    assert seen["ids"] == ["django__django-13741", "pytest-dev__pytest-7571"]
    # Far fewer than the worker count: concurrent pulls are the failure being fixed.
    assert seen["kwargs"]["workers"] <= spec.execution.workers
    assert seen["kwargs"]["timeout"] > 120


def test_official_prepare_refuses_to_start_with_a_missing_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, run_dir = native_spec(tmp_path)
    monkeypatch.setattr(
        swebench_benchmark,
        "prepull",
        lambda *args, **kwargs: ["django__django-13741"],
    )

    with pytest.raises(StageError, match="execution has not started"):
        SwebenchVerified().prepare(spec, run_dir, tmp_path / "cache")


def test_prepull_image_name_matches_the_official_agent_and_grader() -> None:
    """A pre-pull that names the image differently would warm nothing.

    Both consumers must find exactly this reference: mini-swe-agent's `docker run`, and the
    grader's `client.images.get(TestSpec.instance_image_key)` under `--namespace swebench`.
    """
    from minisweagent.run.benchmarks.swebench import get_swebench_docker_image_name

    for instance_id in (
        "matplotlib__matplotlib-22719",
        "scikit-learn__scikit-learn-25232",
        "pylint-dev__pylint-4604",
    ):
        agent_reference = get_swebench_docker_image_name({"instance_id": instance_id})
        grader_key = f"swebench/sweb.eval.x86_64.{instance_id.lower()}:latest".replace(
            "__", "_1776_"
        )
        assert swebench_images.image_name(instance_id) == agent_reference
        assert swebench_images.image_name(instance_id) == f"docker.io/{grader_key}"


def test_prepull_pulls_only_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = ["django__django-13741", "pytest-dev__pytest-7571"]
    monkeypatch.setattr(swebench_images.shutil, "which", lambda _: "/usr/bin/docker")
    # Present in the short spelling `docker images` actually prints, not the pulled one.
    first = swebench_images.image_name(ids[0]).removeprefix("docker.io/")
    second = swebench_images.image_name(ids[1]).removeprefix("docker.io/")
    inventories = iter(({first}, {first, second}))
    monkeypatch.setattr(swebench_images, "_local_images", lambda: next(inventories))
    pulled: list[str] = []
    monkeypatch.setattr(
        swebench_images, "_pull", lambda name, timeout, attempts: pulled.append(name) or None
    )

    failed = swebench_images.prepull(ids, tmp_path / "prepare.log")

    assert failed == []
    assert pulled == [swebench_images.image_name(ids[1])]


def test_prepull_reports_failures_instead_of_killing_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(swebench_images.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(swebench_images, "_local_images", set)
    monkeypatch.setattr(swebench_images, "_pull", lambda *a: "manifest unknown")

    failed = swebench_images.prepull(["django__django-13741"], tmp_path / "prepare.log")

    assert failed == ["django__django-13741"]
    assert "manifest unknown" in (tmp_path / "prepare.log").read_text()


def test_prepull_is_skipped_when_docker_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(swebench_images.shutil, "which", lambda _: None)

    failed = swebench_images.prepull(["django__django-13741"], tmp_path / "prepare.log")

    assert failed == ["django__django-13741"]
    assert "docker is not on PATH" in (tmp_path / "prepare.log").read_text()


def test_harness_config_overrides_the_official_cost_limit_and_pull_timeout(
    tmp_path: Path,
) -> None:
    """`--per-task-cost-limit-usd` used to be decorative for this harness.

    The cap came only from mini-swe-agent's builtin swebench.yaml; nothing the engine resolved
    ever reached the agent. Writing it into a merged config -- last one wins -- is what makes a
    deliberately raised cap take effect.
    """
    spec, run_dir = native_spec(
        tmp_path, per_task_cost_limit_usd=20, allow_cost_limit_override=True
    )
    assert spec.budget.per_task_usd == 20

    path = write_harness_config(spec, run_dir)
    written = yaml.safe_load(path.read_text())

    assert written["agent"]["cost_limit"] == 20
    assert written["environment"]["pull_timeout"] == PULL_TIMEOUT_SECONDS > 120
    command = build_command(spec, run_dir)
    # Applied on top of both the official config and the model profile.
    assert command[-1] == str(harness_config_path(run_dir))
    assert command.index("swebench.yaml") < len(command) - 1


def test_official_profile_rejects_a_raised_cap_without_the_explicit_flag(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="allow-cost-limit-override"):
        native_spec(tmp_path, per_task_cost_limit_usd=20)


def test_swebench_pin_selects_exactly_the_named_instances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SWEBENCH_PIN_INSTANCES", raising=False)
    assert _pinned_ids() is None

    monkeypatch.setenv("SWEBENCH_PIN_INSTANCES", "matplotlib__matplotlib-22719, django__django-1")
    assert _pinned_ids() == ["matplotlib__matplotlib-22719", "django__django-1"]

    pin = tmp_path / "pin.json"
    pin.write_text(json.dumps({"instance_ids": ["matplotlib__matplotlib-25332"]}))
    monkeypatch.setenv("SWEBENCH_PIN_INSTANCES", str(pin))
    assert _pinned_ids() == ["matplotlib__matplotlib-25332"]

    monkeypatch.setenv("SWEBENCH_PIN_INSTANCES", "a,a")
    with pytest.raises(StageError, match="duplicate"):
        _pinned_ids()


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


def test_fallback_run_reaches_the_official_runner_through_the_bootstrap(tmp_path: Path) -> None:
    """A fallback needs a LiteLLM monkeypatch inside the runner subprocess.

    `mini-extra` offers no hook for that, so the same Typer command is reached through a
    bootstrap instead. Without this the flag would be accepted and silently do nothing -- and
    because a refusal is an HTTP 200, nothing in the results would reveal it.
    """
    plain, run_dir = native_spec(tmp_path)
    assert build_command(plain, run_dir)[:4] == ["uv", "run", "mini-extra", "swebench"]

    spec, run_dir = native_spec(tmp_path, anthropic_fallbacks="default")
    command = build_command(spec, run_dir)
    assert command[:3] == ["uv", "run", "python"]
    assert command[3].endswith("mini_swe_agent_bootstrap.py")
    assert Path(command[3]).is_file()
    # Everything after the runner must be identical, or the two paths are not the same experiment.
    assert command[4:] == build_command(plain, run_dir)[4:]


def test_fallback_settings_reach_the_runner_subprocess(tmp_path: Path) -> None:
    from agent_benchmark.agents.mini_swe_agent import ADAPTER
    from agent_benchmark.harnesses.anthropic_fallback import FALLBACKS_ENV, LEDGER_ENV

    spec, run_dir = native_spec(tmp_path)
    assert FALLBACKS_ENV not in ADAPTER.invocation(spec, run_dir, "key").process_environment

    spec, run_dir = native_spec(tmp_path, anthropic_fallbacks="default")
    environment = ADAPTER.invocation(spec, run_dir, "key").process_environment
    assert environment[FALLBACKS_ENV] == "default"
    # The ledger is the only evidence a fallback fired; without it the feature is unfalsifiable.
    assert environment[LEDGER_ENV].endswith("logs/anthropic_fallback.jsonl")


def test_bootstrap_installs_before_importing_the_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Order matters: importing the runner pulls in litellm, which the patch must precede."""
    from agent_benchmark.harnesses import mini_swe_agent_bootstrap as bootstrap
    from agent_benchmark.harnesses.anthropic_fallback import FALLBACKS_ENV, LEDGER_ENV

    monkeypatch.setenv(FALLBACKS_ENV, "default")
    monkeypatch.setenv(LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    seen: list = []
    monkeypatch.setattr(bootstrap, "install_anthropic_fallback", lambda f: seen.append(f) or True)

    bootstrap.install_fallbacks()

    assert seen == ["default"]
