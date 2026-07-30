import json
from pathlib import Path

import pytest
import yaml

from agent_benchmark.agents import agent_adapter
from agent_benchmark.benchmarks.aider_polyglot.pool import create_pool
from agent_benchmark.config.loader import resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.harnesses.aider_native import build_command, prune_nonterminal_results

ROOT = Path(__file__).parents[1]


def aider_spec(
    tmp_path: Path,
    *,
    model: str = "glm-5.2",
    provider: str = "friendli",
    byok: bool = True,
):
    pool = tmp_path / "pool.json"
    create_pool(pool, "random", 2)
    request = UserRequest(
        benchmark="aider-polyglot",
        sampling="random",
        size=2,
        model=model,
        reasoning_effort="xhigh" if model != "kimi-k3" else "max",
        provider=provider,
        byok=byok,
        workers=2,
        budget_usd=10,
    )
    spec = resolve(request, "test-aider-native", ROOT, pool)
    run_dir = tmp_path / "run"
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / spec.benchmark.pool_path).write_text(pool.read_text())
    return spec, run_dir


def test_default_agent_and_native_command(tmp_path: Path) -> None:
    spec, run_dir = aider_spec(tmp_path)
    invocation = agent_adapter("aider").invocation(spec, run_dir, "secret")
    command = build_command(spec, run_dir, invocation)

    assert spec.model.subject_agent == "aider"
    assert spec.model.subject_agent_version == "0.86.0"
    assert command[:2] == ["docker", "run"]
    assert "agent-benchmark.run-id=test-aider-native" in command
    assert command[command.index("--model") + 1] == "openrouter/z-ai/glm-5.2"
    assert command[command.index("--edit-format") + 1] == "diff"
    assert command[command.index("--tries") + 1] == "2"
    assert command[command.index("--threads") + 1] == "2"
    assert "--num-tests" not in command
    assert "--keywords" not in command


def test_friendli_routing_is_written_to_model_settings(tmp_path: Path) -> None:
    spec, run_dir = aider_spec(tmp_path)
    invocation = agent_adapter("aider").invocation(spec, run_dir, "secret")
    settings = yaml.safe_load(Path(invocation.kwargs["model_settings"]).read_text())

    assert settings == [
        {
            "name": "openrouter/z-ai/glm-5.2",
            "edit_format": "diff",
            "accepts_settings": ["reasoning_effort"],
            "extra_params": {
                "extra_body": {
                    "provider": {"only": ["friendli"], "allow_fallbacks": False}
                }
            },
        }
    ]


@pytest.mark.parametrize(
    ("model", "provider", "expected"),
    [
        ("kimi-k3", "openrouter", "openrouter/moonshotai/kimi-k3"),
        ("opus-5", "anthropic", "anthropic/claude-opus-5"),
    ],
)
def test_model_transport(tmp_path: Path, model: str, provider: str, expected: str) -> None:
    spec, run_dir = aider_spec(tmp_path, model=model, provider=provider, byok=False)
    invocation = agent_adapter("aider").invocation(spec, run_dir, "secret")
    assert invocation.model_name == expected


@pytest.mark.parametrize("agent", ["mini-swe-agent", "terminus-2"])
def test_existing_agents_are_rejected_for_aider_polyglot(tmp_path: Path, agent: str) -> None:
    pool = tmp_path / "pool.json"
    create_pool(pool, "random", 1)
    with pytest.raises(ConfigurationError, match="not compatible"):
        resolve(
            UserRequest(
                benchmark="aider-polyglot",
                sampling="random",
                size=1,
                model="glm-5.2",
                agent=agent,
                reasoning_effort="xhigh",
                provider="openrouter",
                workers=1,
                budget_usd=5,
            ),
            f"test-{agent}",
            ROOT,
            pool,
        )


def test_pool_contains_stable_ids(tmp_path: Path) -> None:
    spec, run_dir = aider_spec(tmp_path)
    pool = json.loads((run_dir / spec.benchmark.pool_path).read_text())
    assert all("/" in identity for identity in pool["instance_ids"])


def test_resume_preserves_terminal_results_and_prunes_malformed(tmp_path: Path) -> None:
    spec, run_dir = aider_spec(tmp_path)
    root = run_dir / "artifacts" / "aider_native" / spec.run_id / "python" / "exercises"
    valid = root / "practice" / "valid" / ".aider.results.json"
    malformed = root / "practice" / "malformed" / ".aider.results.json"
    incomplete = root / "practice" / "incomplete" / ".aider.results.json"
    model_error = root / "practice" / "model-error" / ".aider.results.json"
    for path in (valid, malformed, incomplete, model_error):
        path.parent.mkdir(parents=True, exist_ok=True)
    valid.write_text(json.dumps({"tests_outcomes": [False, True]}))
    malformed.write_text("{broken")
    incomplete.write_text(json.dumps({"tests_outcomes": []}))
    model_error.write_text(
        json.dumps(
            {
                "tests_outcomes": [False, False],
                "num_error_outputs": 2,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
        )
    )

    removed = prune_nonterminal_results(spec, run_dir)

    assert valid.exists()
    assert set(removed) == {malformed, incomplete, model_error}
    assert not malformed.exists()
    assert not incomplete.exists()
    assert not model_error.exists()
