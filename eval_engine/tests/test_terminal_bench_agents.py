import json
from pathlib import Path

import pytest

from agent_benchmark.agents import agent_adapter
from agent_benchmark.benchmarks.terminal_bench.pool import create_pool
from agent_benchmark.config.loader import list_profiles, resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import ConfigurationError

ROOT = Path(__file__).parents[1]


def resolved_spec(tmp_path: Path, benchmark: str):
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
    assert list_profiles()["agents"] == ["mini-swe-agent", "terminus-2"]


def test_agent_adapters_are_loaded_from_separate_modules() -> None:
    assert agent_adapter("mini-swe-agent").__class__.__module__.endswith(".mini_swe_agent")
    assert agent_adapter("terminus-2").__class__.__module__.endswith(".terminus_2")
    with pytest.raises(ConfigurationError, match="not available"):
        agent_adapter("unknown-agent")
