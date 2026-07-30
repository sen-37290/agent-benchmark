import json
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_benchmark.agents import agent_adapter
from agent_benchmark.benchmarks.swebench_verified.benchmark import SwebenchVerified
from agent_benchmark.benchmarks.terminal_bench.pool import create_pool
from agent_benchmark.cli import app
from agent_benchmark.config.loader import list_profiles, resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import ConfigurationError

ROOT = Path(__file__).parents[1]


def resolved_spec(tmp_path: Path, benchmark: str, agent: str | None = None):
    pool = tmp_path / "pool.json"
    pool.parent.mkdir(parents=True, exist_ok=True)
    if benchmark == "terminal-bench-2.1":
        create_pool(pool, "random", 1)
    else:
        pool.write_text(json.dumps({"instance_ids": ["django__django-13741"]}))
    return resolve(
        UserRequest(
            benchmark=benchmark,
            sampling="random" if benchmark == "terminal-bench-2.1" else None,
            size=1 if benchmark == "terminal-bench-2.1" else None,
            model="glm-5.2",
            agent=agent,
            reasoning_effort="xhigh",
            provider="friendli",
            byok=True,
            workers=1,
            budget_usd=5,
        ),
        f"test-agent-{benchmark}",
        ROOT,
        pool,
    )


def test_benchmark_resolves_independent_agent_profiles(tmp_path: Path) -> None:
    terminal = resolved_spec(tmp_path / "terminal", "terminal-bench-2.1")
    swebench = resolved_spec(tmp_path / "swebench", "swebench-verified")

    assert (terminal.model.subject_agent, terminal.model.subject_agent_version) == (
        "terminus-2",
        "2.0.0",
    )
    assert (swebench.model.subject_agent, swebench.model.subject_agent_version) == (
        "mini-swe-agent",
        "2.4.5",
    )
    assert list_profiles()["agents"] == ["aider", "mini-swe-agent", "terminus-2"]


def test_model_profiles_do_not_select_agents() -> None:
    config_root = files("agent_benchmark.config")
    for profile in list_profiles()["models"]:
        model = yaml.safe_load(config_root.joinpath("models", f"{profile}.yaml").read_text())
        assert "subject_agent_profile" not in model
        assert "default_agent" not in model


def test_cli_agent_overrides_benchmark_and_model_defaults(tmp_path: Path) -> None:
    swebench = resolved_spec(tmp_path / "swebench", "swebench-verified-harbor", "terminus-2")
    terminal = resolved_spec(tmp_path / "terminal", "terminal-bench-2.1", "mini-swe-agent")

    assert (swebench.model.subject_agent, swebench.model.subject_agent_version) == (
        "terminus-2",
        "2.0.0",
    )
    assert (terminal.model.subject_agent, terminal.model.subject_agent_version) == (
        "mini-swe-agent",
        "2.4.5",
    )


def test_unknown_cli_agent_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="unknown agent profile 'unknown-agent'"):
        resolved_spec(tmp_path, "swebench-verified", "unknown-agent")


def test_cli_plan_overrides_swebench_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    def create_test_pool(
        self: SwebenchVerified,
        output_path: Path,
        sampling: str | None,
        size: int | None,
    ) -> None:
        del self, sampling, size
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"instance_ids": ["django__django-13741"]}))

    monkeypatch.setattr(SwebenchVerified, "create_pool", create_test_pool)
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "--benchmark",
            "swebench-verified-harbor",
            "--model",
            "glm-5.2",
            "--agent",
            "terminus-2",
            "--reasoning-effort",
            "xhigh",
            "--provider",
            "friendli",
            "--byok",
            "--workers",
            "1",
            "--budget-usd",
            "5",
            "--sampling",
            "random",
            "--size",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "profile: swebench-verified-harbor" in result.output
    assert "subject_agent: terminus-2" in result.output
    assert "subject_agent_version: 2.0.0" in result.output


def test_official_swebench_rejects_non_mini_agent(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not compatible"):
        resolved_spec(tmp_path, "swebench-verified", "terminus-2")


def test_cli_plan_allows_provider_default_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    def create_test_pool(
        self: SwebenchVerified,
        output_path: Path,
        sampling: str | None,
        size: int | None,
    ) -> None:
        del self, sampling, size
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"instance_ids": ["django__django-13741"]}))

    monkeypatch.setattr(SwebenchVerified, "create_pool", create_test_pool)
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "--benchmark",
            "swebench-verified",
            "--model",
            "opus-5",
            "--provider",
            "anthropic",
            "--workers",
            "1",
            "--budget-usd",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "reasoning_effort: null" in result.output
    assert "reasoning_effort: max" not in result.output


def test_agent_adapters_are_loaded_from_separate_modules() -> None:
    assert agent_adapter("mini-swe-agent").__class__.__module__.endswith(".mini_swe_agent")
    assert agent_adapter("terminus-2").__class__.__module__.endswith(".terminus_2")
    with pytest.raises(ConfigurationError, match="not available"):
        agent_adapter("unknown-agent")
