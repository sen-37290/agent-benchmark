from __future__ import annotations

import json
import re
from pathlib import Path

from agent_benchmark.agents import agent_adapter
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.harnesses.base import HarnessAdapter
from agent_benchmark.run.process import run_logged

VALID_TERMINAL_STATUSES = frozenset(
    {"Submitted", "LimitsExceeded", "TimeExceeded", "RepeatedFormatError"}
)


def output_dir(run_dir: Path) -> Path:
    return run_dir / "artifacts" / "minisweagent_swebench"


def _pool_ids(spec: ResolvedSpec, run_dir: Path) -> list[str]:
    return json.loads((run_dir / spec.benchmark.pool_path).read_text())["instance_ids"]


def _instance_filter(ids: list[str]) -> str:
    return rf"^(?:{'|'.join(re.escape(task_id) for task_id in ids)})$"


def collected_cost(directory: Path) -> float:
    total = 0.0
    for trajectory in directory.glob("*/*.traj.json"):
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


def build_command(spec: ResolvedSpec, run_dir: Path) -> list[str]:
    ids = _pool_ids(spec, run_dir)
    return [
        "uv",
        "run",
        "mini-extra",
        "swebench",
        "--subset",
        spec.benchmark.dataset_id,
        "--split",
        "test",
        "--filter",
        _instance_filter(ids),
        "--output",
        str(output_dir(run_dir)),
        "--workers",
        str(spec.execution.workers),
        "--config",
        "swebench.yaml",
        "--config",
        str(run_dir / "subject-config.yaml"),
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
        budget_usd = (
            None if spec.model.provider == "friendli" and spec.model.byok else spec.budget.total_usd
        )
        directory = output_dir(run_dir)
        directory.mkdir(parents=True, exist_ok=True)
        run_logged(
            build_command(spec, run_dir),
            cwd=run_dir,
            log_path=run_dir / "logs" / "execute.log",
            env=process_environment,
            redact_values=[api_key],
            cost_reader=lambda: collected_cost(directory),
            budget_usd=budget_usd,
        )

        predictions_path = directory / "preds.json"
        if not predictions_path.is_file():
            raise StageError("official mini-swe-agent runner did not produce preds.json")
        predictions = json.loads(predictions_path.read_text())
        expected = set(_pool_ids(spec, run_dir))
        actual = set(predictions)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise StageError(
                "official mini-swe-agent predictions do not match the pool: "
                f"missing={missing[:3]}, extra={extra[:3]}"
            )
        invalid = [
            task_id
            for task_id, prediction in predictions.items()
            if not isinstance(prediction, dict)
            or prediction.get("instance_id") != task_id
            or not isinstance(prediction.get("model_name_or_path"), str)
            or not isinstance(prediction.get("model_patch"), str)
        ]
        if invalid:
            raise StageError(
                "official mini-swe-agent produced invalid predictions for: "
                + ", ".join(sorted(invalid)[:3])
            )
        failures = task_failures(directory, _pool_ids(spec, run_dir))
        if failures:
            examples = ", ".join(
                f"{task_id}={status}" for task_id, status in sorted(failures.items())[:3]
            )
            raise StageError(
                f"official mini-swe-agent reported task errors for {len(failures)}/{len(expected)} "
                f"instances ({examples})"
            )
