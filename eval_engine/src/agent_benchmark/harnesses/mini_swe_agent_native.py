from __future__ import annotations

import contextlib
import json
import re
import shutil
from pathlib import Path

import yaml

from agent_benchmark.agents import agent_adapter
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.harnesses.base import HarnessAdapter
from agent_benchmark.run.process import run_logged
from agent_benchmark.run.retry import (
    is_transient,
    load_manifest,
    missing_output_is_retryable,
    pending_tasks,
    record_attempt,
    save_manifest,
    select_attempt,
    wait_before_attempt,
)
from agent_benchmark.run.stop import should_stop

VALID_TERMINAL_STATUSES = frozenset(
    {"Submitted", "LimitsExceeded", "TimeExceeded", "RepeatedFormatError"}
)


# Anything the ENGINE has to impose on the official agent config, kept apart from
# subject-config.yaml so the two stay readable: that file is the model profile, this one is the
# harness. mini-swe-agent merges `--config` files recursively with the last one winning, so this
# is applied on top of the official swebench.yaml.
HARNESS_CONFIG_NAME = "harness-config.yaml"
# The official swebench.yaml leaves DockerEnvironmentConfig.pull_timeout at its 120-second
# default, which wraps `docker run` -- image pull included. `prepare` now pre-pulls every image
# in the pool, so this bound should never be reached; it is raised anyway so that a cache miss
# (a task added later, an evicted layer, a fresh VM) costs time instead of costing the task.
PULL_TIMEOUT_SECONDS = 1800


def output_dir(run_dir: Path) -> Path:
    return run_dir / "artifacts" / "minisweagent_swebench"


def harness_config_path(run_dir: Path) -> Path:
    return run_dir / HARNESS_CONFIG_NAME


def write_harness_config(spec: ResolvedSpec, run_dir: Path) -> Path:
    """Write the engine's overrides of the official agent config, and return its path.

    Two settings live here:

    `agent.cost_limit` -- until now the per-task dollar cap for this harness came only from
    mini-swe-agent's builtin swebench.yaml, because the adapter's MSWEA_GLOBAL_COST_LIMIT is
    popped below (it applies to the whole batch process, not per task) and subject-config.yaml
    never set it. `--per-task-cost-limit-usd` was therefore decorative here: it was validated
    against the benchmark profile and then never reached the agent. Writing it makes the resolved
    spec authoritative, which is what lets a re-run raise the cap deliberately.

    `environment.pull_timeout` -- see PULL_TIMEOUT_SECONDS.
    """
    path = harness_config_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "agent": {"cost_limit": float(spec.budget.per_task_usd)},
                "environment": {"pull_timeout": PULL_TIMEOUT_SECONDS},
            },
            sort_keys=False,
        )
    )
    return path


def _pool_ids(spec: ResolvedSpec, run_dir: Path) -> list[str]:
    return json.loads((run_dir / spec.benchmark.pool_path).read_text())["instance_ids"]


def _instance_filter(ids: list[str]) -> str:
    return rf"^(?:{'|'.join(re.escape(task_id) for task_id in ids)})$"


def collected_cost(directory: Path) -> float:
    total = 0.0
    for trajectory in directory.rglob("*.traj.json"):
        try:
            data = json.loads(trajectory.read_text())
            total += float(data.get("info", {}).get("model_stats", {}).get("instance_cost", 0))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return total


def task_failures(directory: Path, ids: list[str]) -> dict[str, str]:
    failures: dict[str, str] = {}
    for task_id in ids:
        trajectory_path = directory / task_id / f"{task_id}.traj.json"
        if not trajectory_path.is_file():
            failures[task_id] = "MissingTrajectory"
            continue
        try:
            trajectory = json.loads(trajectory_path.read_text())
        except (OSError, json.JSONDecodeError):
            failures[task_id] = "InvalidTrajectory"
            continue
        exit_status = str(trajectory.get("info", {}).get("exit_status") or "MissingExitStatus")
        if exit_status not in VALID_TERMINAL_STATUSES:
            failures[task_id] = exit_status
    return failures


def _runner(spec: ResolvedSpec) -> list[str]:
    """How to invoke the official SWE-bench runner.

    Normally the console script, unchanged. When the run asks for a provider fallback, the same
    Typer command has to be reached through a bootstrap instead, because the LiteLLM monkeypatch
    must be installed inside this subprocess before its first request -- `mini-extra` offers no
    hook for that. Every other run keeps the byte-identical official invocation.
    """
    if spec.model.anthropic_fallbacks or spec.model.openai_fallbacks:
        bootstrap = Path(__file__).with_name("mini_swe_agent_bootstrap.py")
        return ["uv", "run", "python", str(bootstrap)]
    return ["uv", "run", "mini-extra", "swebench"]


def build_command(
    spec: ResolvedSpec,
    run_dir: Path,
    ids: list[str] | None = None,
    destination: Path | None = None,
) -> list[str]:
    ids = ids or _pool_ids(spec, run_dir)
    return [
        *_runner(spec),
        "--subset",
        spec.benchmark.dataset_id,
        "--split",
        "test",
        "--filter",
        _instance_filter(ids),
        "--output",
        str(destination or output_dir(run_dir)),
        "--workers",
        str(spec.execution.workers),
        "--config",
        "swebench.yaml",
        "--config",
        str(run_dir / "subject-config.yaml"),
        # Last wins in mini-swe-agent's recursive merge, so the engine's overrides sit on top of
        # both the official config and the model profile.
        "--config",
        str(harness_config_path(run_dir)),
    ]


class MiniSweAgentNativeHarness(HarnessAdapter):
    name = "mini-swe-agent-native"

    def execute(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        cache_root: Path,
        secrets: dict[str, str],
    ) -> None:
        del cache_root
        if spec.model.subject_agent != "mini-swe-agent":
            raise ConfigurationError("native SWE-bench execution requires mini-swe-agent")
        api_key = secrets.get(spec.model.api_key_env)
        if not api_key:
            raise ConfigurationError(f"required secret {spec.model.api_key_env!r} was not supplied")

        invocation = agent_adapter(spec.model.subject_agent).invocation(spec, run_dir, api_key)
        process_environment = dict(invocation.process_environment)
        # The official config applies the $3 limit independently to each agent. The adapter's
        # global limit is appropriate when Harbor launches one process per task, not for this
        # shared batch process; the engine monitors the separate run-wide budget below.
        process_environment.pop("MSWEA_GLOBAL_COST_LIMIT", None)
        write_harness_config(spec, run_dir)
        budget_usd = (
            None if spec.model.provider == "friendli" and spec.model.byok else spec.budget.total_usd
        )
        ids = _pool_ids(spec, run_dir)
        attempts_root = run_dir / "artifacts" / "retry_attempts" / "mini_swe_agent_native"
        manifest = load_manifest(run_dir, ids)
        max_attempts = spec.execution.error_retries + 1
        for attempt in range(1, max_attempts + 1):
            all_pending = pending_tasks(manifest, max_attempts)
            save_manifest(run_dir, manifest)
            if not all_pending:
                break
            pending = [
                task_id
                for task_id in all_pending
                if len(manifest["tasks"][task_id]["attempts"]) == attempt - 1
            ]
            if not pending:
                continue
            wait_before_attempt(attempt)
            round_dir = attempts_root / f"attempt-{attempt:02d}"
            round_dir.mkdir(parents=True, exist_ok=True)
            # A round failure is recorded per task from its trajectories below and retried on
            # the next attempt; only a stop request ends the loop early.
            with contextlib.suppress(StageError):
                run_logged(
                    build_command(spec, run_dir, pending, round_dir),
                    cwd=run_dir,
                    log_path=run_dir / "logs" / "execute.log",
                    env=process_environment,
                    redact_values=[api_key],
                    cost_reader=lambda: collected_cost(attempts_root),
                    budget_usd=budget_usd,
                    stop_dir=run_dir,
                )
            predictions_path = round_dir / "preds.json"
            predictions = (
                json.loads(predictions_path.read_text()) if predictions_path.is_file() else {}
            )
            failures = task_failures(round_dir, pending)
            for task_id in pending:
                trajectory_path = round_dir / task_id / f"{task_id}.traj.json"
                cost = None
                message = None
                exit_status = None
                if trajectory_path.is_file():
                    try:
                        trajectory = json.loads(trajectory_path.read_text())
                        info = trajectory.get("info", {})
                        exit_status = str(info.get("exit_status") or "") or None
                        value = info.get("model_stats", {}).get("instance_cost")
                        cost = float(value) if value is not None else None
                        message = str(info.get("exception_str") or "")
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        pass
                error_type = failures.get(task_id)
                valid_prediction = isinstance(predictions.get(task_id), dict)
                retryable = (
                    missing_output_is_retryable(error_type, message, exit_status)
                    if not valid_prediction
                    else is_transient(error_type, message)
                )
                status = "completed" if error_type is None and valid_prediction else "error"
                record_attempt(
                    manifest,
                    task_id,
                    attempt=attempt,
                    artifact=str(round_dir.relative_to(run_dir)),
                    status=status,
                    error_type=error_type or (None if valid_prediction else "MissingPrediction"),
                    retryable=retryable,
                    cost_usd=cost,
                )
                if status == "completed" or not retryable:
                    select_attempt(manifest, task_id, attempt)
            save_manifest(run_dir, manifest)
            if should_stop(run_dir):
                # Cost cap or operator stop: keep this round's results, start no more.
                break

        pending_tasks(manifest, max_attempts)
        save_manifest(run_dir, manifest)
        directory = output_dir(run_dir)
        directory.mkdir(parents=True, exist_ok=True)
        canonical_predictions: dict[str, object] = {}
        for task_id in ids:
            task = manifest["tasks"][task_id]
            selected = task["selected_attempt"]
            item = next((entry for entry in task["attempts"] if entry["attempt"] == selected), None)
            if item is None:
                # The run stopped before this task was attempted. Omit it entirely so the
                # official grader scores only what actually ran, and normalize reports it
                # as unrun rather than as an empty (failing) prediction.
                continue
            source = run_dir / item["artifact"]
            source_task = source / task_id
            if source_task.is_dir():
                shutil.copytree(source_task, directory / task_id, dirs_exist_ok=True)
            round_predictions_path = source / "preds.json"
            round_predictions = (
                json.loads(round_predictions_path.read_text())
                if round_predictions_path.is_file()
                else {}
            )
            canonical_predictions[task_id] = round_predictions.get(task_id) or {
                "model_name_or_path": spec.model.model_id,
                "instance_id": task_id,
                "model_patch": "",
            }
        (directory / "preds.json").write_text(json.dumps(canonical_predictions, indent=2) + "\n")
