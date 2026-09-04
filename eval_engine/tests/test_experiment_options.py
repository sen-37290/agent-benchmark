"""Coverage for the per-experiment run options used by the ten-VM fleet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_benchmark.cli import _new_run_id, _request
from agent_benchmark.config.loader import resolve
from agent_benchmark.exceptions import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pool(tmp_path: Path, ids: list[str]) -> Path:
    path = tmp_path / "pool.json"
    path.write_text(json.dumps({"instance_ids": ids}))
    return path


def _make_request(**overrides):
    """Build a UserRequest from named defaults.

    Keyword-only on purpose: _request takes seventeen parameters, and calling it positionally
    meant every new option silently shifted the remaining arguments along.
    """
    defaults: dict = {
        "benchmark": "terminal-bench-2.1",
        "sampling": None,
        "size": None,
        "model": "fable-5-1",
        "agent": None,
        "reasoning_effort": None,
        "provider": "anthropic",
        "provider_route": None,
        "byok": False,
        "workers": 4,
        "budget_usd": 500.0,
        "no_budget_limit": False,
        "per_task_cost_limit_usd": None,
        "no_timeout": False,
        "agent_timeout_multiplier": None,
        "error_retries": None,
        "target": "primary",
    }
    return _request(**{**defaults, **overrides})


def _resolved(tmp_path: Path, **overrides):
    request = _make_request(**overrides)
    return resolve(request, "test-run", PROJECT_ROOT, _pool(tmp_path, ["a", "b"]))


def test_label_prefixes_run_id() -> None:
    run_id = _new_run_id("terminal-bench-2.1", "fable-5-1", "sen-fable-5-1-terminal-bench")
    assert "sen-fable-5-1-terminal-bench" in run_id
    # Timestamp first so runs still sort chronologically; random suffix keeps IDs unique.
    assert run_id.split("-")[0].endswith("Z")
    assert run_id != _new_run_id("terminal-bench-2.1", "fable-5-1", "sen-fable-5-1-terminal-bench")


def test_label_absent_keeps_benchmark_model_slug() -> None:
    assert "terminal-bench-2.1-fable-5-1" in _new_run_id("terminal-bench-2.1", "fable-5-1")


def test_api_key_from_overrides_only_the_source(tmp_path: Path) -> None:
    spec = _resolved(tmp_path, api_key_from="SEN_FABLE_5_1_TERMINAL_BENCH")
    # The subject agent must still see the canonical provider variable...
    assert spec.model.api_key_env == "ANTHROPIC_API_KEY"
    # ...while the orchestrator reads this experiment's own key.
    assert spec.model.api_key_source_env == "SEN_FABLE_5_1_TERMINAL_BENCH"


def test_api_key_source_defaults_to_canonical(tmp_path: Path) -> None:
    spec = _resolved(tmp_path)
    assert spec.model.api_key_source_env == "ANTHROPIC_API_KEY"


def test_no_cleanup_is_recorded(tmp_path: Path) -> None:
    assert _resolved(tmp_path, no_cleanup=True).execution.no_cleanup is True
    assert _resolved(tmp_path).execution.no_cleanup is False


def test_label_is_recorded_in_spec(tmp_path: Path) -> None:
    spec = _resolved(tmp_path, label="sen-fable-5-1-terminal-bench")
    assert spec.label == "sen-fable-5-1-terminal-bench"


def test_terminal_bench_carries_a_twenty_dollar_per_task_cap(tmp_path: Path) -> None:
    # Raised from an unenforced $5 to an ENFORCED $20. The cap used to be opted out of entirely,
    # and one Terminal-Bench task then ran up $5,019 on its own.
    assert _resolved(tmp_path).budget.per_task_usd == 20.0


def test_openai_models_resolve_with_provider_default_effort(tmp_path: Path) -> None:
    request = _make_request(
        model="gpt-5-6-sol",
        provider="openai",
        reasoning_effort=None,  # no reasoning effort: use the provider default
        api_key_from="SEN_GPT_5_6_SOL_TERMINAL_BENCH",
    )
    spec = resolve(request, "test-run", PROJECT_ROOT, _pool(tmp_path, ["a"]))
    assert spec.model.api == "openai"
    assert spec.model.model_id == "gpt-5.6-sol"
    assert spec.model.reasoning_effort is None
    # An unset effort must not be materialised anywhere in the subject agent's config.
    assert "reasoning_effort" not in spec.model.config["model"].get("model_kwargs", {})


def test_model_cannot_use_a_mismatched_transport(tmp_path: Path) -> None:
    request = _make_request(model="gpt-5-6-sol", provider="anthropic")
    with pytest.raises(ConfigurationError):
        resolve(request, "test-run", PROJECT_ROOT, _pool(tmp_path, ["a"]))


@pytest.mark.parametrize(
    ("model", "provider", "expected"),
    [
        ("gpt-5-6-sol", "openai", "openai/gpt-5.6-sol"),
        ("gpt-5-6-terra", "openai", "openai/gpt-5.6-terra"),
        ("gpt-5-6-luna", "openai", "openai/gpt-5.6-luna"),
        ("fable-5-1", "anthropic", "anthropic/claude-fable-5-1"),
        # opus-5's profile already carries the prefix; it must not be doubled.
        ("opus-5", "anthropic", "anthropic/claude-opus-5"),
        ("glm-5.3", "openrouter", "openrouter/z-ai/glm-5.3"),
    ],
)
def test_litellm_prefix_per_transport(tmp_path: Path, model, provider, expected) -> None:
    from agent_benchmark.agents.base import litellm_model_name

    request = _make_request(model=model, provider=provider)
    spec = resolve(request, "test-run", PROJECT_ROOT, _pool(tmp_path, ["a"]))
    assert litellm_model_name(spec) == expected


def test_terminus_per_task_limit_follows_the_benchmark_setting(tmp_path: Path) -> None:
    from agent_benchmark.agents import agent_adapter
    from agent_benchmark.run.costguard import LIMIT_ENV

    spec = _resolved(tmp_path)
    invocation = agent_adapter("terminus-2").invocation(spec, tmp_path, "secret-key")
    # Terminal-Bench now ENFORCES the cap, so the limit reaches the guard inside the Harbor
    # process. Without it the guard installs nothing and a single task's spend is unbounded.
    assert invocation.process_environment[LIMIT_ENV] == "20.000000"
    # Host-only: the key must never be in `environment`, which is what crosses into the container.
    assert invocation.environment == {}
    assert invocation.process_environment["ANTHROPIC_API_KEY"] == "secret-key"

    # A benchmark that opts out passes no limit, and the guard then installs nothing.
    spec.benchmark.settings["enforce_per_task_cost_limit"] = False
    invocation = agent_adapter("terminus-2").invocation(spec, tmp_path, "secret-key")
    assert LIMIT_ENV not in invocation.process_environment
