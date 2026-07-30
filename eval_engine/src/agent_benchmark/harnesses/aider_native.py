from __future__ import annotations

import json
from pathlib import Path

from agent_benchmark.agents import agent_adapter
from agent_benchmark.agents.base import AgentInvocation
from agent_benchmark.benchmarks.aider_polyglot.results import model_call_failed
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.harnesses.base import HarnessAdapter
from agent_benchmark.run.process import run_logged


def native_root(run_dir: Path) -> Path:
    return run_dir / "artifacts" / "aider_native"


def collected_cost(spec: ResolvedSpec, run_dir: Path) -> float:
    total = 0.0
    root = native_root(run_dir) / spec.run_id
    for path in root.glob("*/exercises/practice/*/.aider.results.json"):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        value = raw.get("cost")
        if isinstance(value, int | float) and not isinstance(value, bool):
            total += float(value)
    return total


def is_terminal_result(path: Path) -> bool:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    if model_call_failed(raw):
        return False
    outcomes = raw.get("tests_outcomes")
    return bool(raw.get("exception")) or (
        isinstance(outcomes, list) and any(isinstance(value, bool) for value in outcomes)
    )


def prune_nonterminal_results(spec: ResolvedSpec, run_dir: Path) -> list[Path]:
    root = native_root(run_dir) / spec.run_id
    removed: list[Path] = []
    for path in root.glob("*/exercises/practice/*/.aider.results.json"):
        if not is_terminal_result(path):
            path.unlink()
            removed.append(path)
    return removed


def build_command(
    spec: ResolvedSpec,
    run_dir: Path,
    invocation: AgentInvocation,
) -> list[str]:
    root = native_root(run_dir)
    settings_path = Path(str(invocation.kwargs["model_settings"]))
    if settings_path.parent != root:
        raise ConfigurationError("Aider model settings must be inside the native artifact root")
    command = [
        "docker",
        "run",
        "--rm",
        "--memory=12g",
        "--memory-swap=12g",
        "--label",
        f"agent-benchmark.run-id={spec.run_id}",
        "--volume",
        f"{root}:/benchmarks",
        "--env",
        spec.model.api_key_env,
        "--env",
        "AIDER_DOCKER=1",
        "--env",
        "AIDER_BENCHMARK_DIR=/benchmarks",
        str(spec.benchmark.settings["image"]),
        "/aider/benchmark/benchmark.py",
        f"/benchmarks/{spec.run_id}",
        "--model",
        invocation.model_name,
        "--edit-format",
        str(invocation.kwargs["edit_format"]),
        "--reasoning-effort",
        str(invocation.kwargs["reasoning_effort"]),
        "--read-model-settings",
        f"/benchmarks/{settings_path.name}",
        "--threads",
        str(spec.execution.workers),
        "--tries",
        str(spec.benchmark.settings["attempts"]),
        "--exercises-dir",
        "polyglot-benchmark",
        "--cont",
    ]
    return command


class AiderNativeHarness(HarnessAdapter):
    name = "aider-native"

    def execute(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        cache_root: Path,
        secrets: dict[str, str],
    ) -> None:
        del cache_root
        api_key = secrets.get(spec.model.api_key_env)
        if not api_key:
            raise ConfigurationError(f"required secret {spec.model.api_key_env!r} was not supplied")
        invocation = agent_adapter(spec.model.subject_agent).invocation(spec, run_dir, api_key)
        root = native_root(run_dir)
        root.mkdir(parents=True, exist_ok=True)
        metadata = {
            "run_id": spec.run_id,
            "benchmark": spec.benchmark.profile,
            "model_profile": spec.model.profile,
            "model_id": spec.model.model_id,
            "subject_agent": spec.model.subject_agent,
            "subject_agent_version": spec.model.subject_agent_version,
            "edit_format": invocation.kwargs["edit_format"],
            "reasoning_effort": spec.model.reasoning_effort,
            "workers": spec.execution.workers,
        }
        (root / "run_meta.json").write_text(json.dumps(metadata, indent=2) + "\n")
        prune_nonterminal_results(spec, run_dir)
        run_logged(
            build_command(spec, run_dir, invocation),
            cwd=run_dir,
            log_path=run_dir / "logs" / "execute.log",
            env=invocation.process_environment,
            redact_values=[api_key],
            cost_reader=lambda: collected_cost(spec, run_dir),
            budget_usd=spec.budget.total_usd,
        )
        paths = sorted((root / spec.run_id).glob("*/exercises/practice/*/.aider.results.json"))
        if len(paths) != spec.benchmark.sample_size:
            actual = len(paths)
            expected = spec.benchmark.sample_size
            raise StageError(f"Aider native produced {actual} results for {expected} tasks")
        nonterminal = [path for path in paths if not is_terminal_result(path)]
        if nonterminal:
            raise StageError(
                f"Aider native produced {len(nonterminal)} malformed or non-terminal results"
            )
