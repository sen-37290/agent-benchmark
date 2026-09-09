from __future__ import annotations

import json
import shutil
from pathlib import Path

from agent_benchmark.agents import agent_adapter
from agent_benchmark.agents.base import AgentInvocation
from agent_benchmark.benchmarks.paths import benchmark_dataset_dir
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.harnesses.base import HarnessAdapter
from agent_benchmark.run.process import collected_cost, run_logged
from agent_benchmark.run.retry import (
    is_transient,
    load_manifest,
    pending_tasks,
    record_attempt,
    save_manifest,
    select_attempt,
    wait_before_attempt,
)


def harbor_job_dir(spec: ResolvedSpec, run_dir: Path) -> Path:
    return run_dir / "artifacts" / "harbor_jobs" / spec.run_id


def _agent_arguments(invocation: AgentInvocation) -> list[str]:
    arguments: list[str] = []
    for key, value in invocation.kwargs.items():
        encoded = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        arguments.extend(["--ak", f"{key}={encoded}"])
    for key, value in invocation.environment.items():
        arguments.extend(["--ae", f"{key}={value}"])
    return arguments


def build_command(
    spec: ResolvedSpec,
    run_dir: Path,
    cache_root: Path,
    api_key: str,
    invocation: AgentInvocation | None = None,
    task_ids: list[str] | None = None,
    output_dir: Path | None = None,
    job_name: str | None = None,
    engine_managed_retries: bool = False,
) -> list[str]:
    output_dir = output_dir or run_dir / "artifacts" / "harbor_jobs"
    job_name = job_name or spec.run_id
    job_dir = output_dir / job_name
    if (job_dir / "config.json").is_file():
        return ["uv", "run", "harbor", "job", "resume", "-p", str(job_dir)]

    invocation = invocation or agent_adapter(spec.model.subject_agent).invocation(
        spec, run_dir, api_key
    )
    command = ["uv", "run", "harbor", "run"]
    dataset_source = spec.benchmark.settings.get("dataset_source", "local")
    if dataset_source == "package":
        pool = json.loads((run_dir / spec.benchmark.pool_path).read_text())
        task_name_prefix = str(spec.benchmark.settings.get("task_name_prefix", ""))
        command.extend(["-d", spec.benchmark.dataset_id])
        for task_id in task_ids or pool["instance_ids"]:
            command.extend(["--include-task-name", f"{task_name_prefix}{task_id}"])
    elif dataset_source == "local":
        command.extend(["-p", str(benchmark_dataset_dir(spec, cache_root))])
    else:
        raise ConfigurationError(f"unsupported Harbor dataset source: {dataset_source!r}")
    command.extend(
        [
            "-a",
            spec.model.subject_agent,
            "-m",
            invocation.model_name,
            *_agent_arguments(invocation),
            "-e",
            spec.execution.environment,
            "-n",
            str(spec.execution.workers),
            "-k",
            str(spec.benchmark.settings.get("attempts", 1)),
            "-o",
            str(output_dir),
            "--job-name",
            job_name,
            "--yes",
        ]
    )
    if spec.execution.no_timeout:
        command.extend(["--agent-timeout-multiplier", "inf"])
    if spec.execution.error_retries and not engine_managed_retries:
        command.extend(["--max-retries", str(spec.execution.error_retries)])
    return command


class HarborHarness(HarnessAdapter):
    name = "harbor"

    def execute(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        cache_root: Path,
        secrets: dict[str, str],
    ) -> None:
        api_key = secrets.get(spec.model.api_key_env)
        if not api_key:
            raise ConfigurationError(f"required secret {spec.model.api_key_env!r} was not supplied")

        output_dir = run_dir / "artifacts" / "harbor_jobs"
        output_dir.mkdir(parents=True, exist_ok=True)
        invocation = agent_adapter(spec.model.subject_agent).invocation(spec, run_dir, api_key)

        metadata = {
            "run_id": spec.run_id,
            "benchmark": spec.benchmark.profile,
            "sampling": spec.benchmark.sampling,
            "sample_size": spec.benchmark.sample_size,
            "model_profile": spec.model.profile,
            "model_id": spec.model.model_id,
            "subject_agent": spec.model.subject_agent,
            "subject_agent_version": spec.model.subject_agent_version,
            "api": spec.model.api,
            "reasoning_effort": spec.model.reasoning_effort,
            "workers": spec.execution.workers,
        }
        (run_dir / "artifacts" / "run_meta.json").write_text(json.dumps(metadata, indent=2) + "\n")

        if spec.benchmark.profile == "terminal-bench-2.1":
            self._execute_terminal_with_retries(spec, run_dir, cache_root, api_key, invocation)
            return

        command = build_command(spec, run_dir, cache_root, api_key, invocation)

        run_logged(
            command,
            cwd=run_dir,
            log_path=run_dir / "logs" / "execute.log",
            env=invocation.process_environment,
            redact_values=[api_key],
            budget_job_dir=output_dir,
            budget_usd=spec.budget.total_usd,
        )

        trial_results = sorted(output_dir.glob("*/*/result.json"))
        if len(trial_results) != spec.benchmark.sample_size:
            raise StageError(
                "Harbor produced "
                f"{len(trial_results)} trial results for {spec.benchmark.sample_size} tasks"
            )
        exceptions = []
        for result_path in trial_results:
            result = json.loads(result_path.read_text())
            if result.get("exception_info"):
                exceptions.append(result_path.parent.name)
        if exceptions and spec.benchmark.settings.get("fail_on_trial_exception", True):
            raise StageError(
                f"Harbor reported task exceptions for {len(exceptions)}/{len(trial_results)} "
                f"trials; inspect logs/execute.log (examples: {', '.join(exceptions[:3])})"
            )

    def _execute_terminal_with_retries(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        cache_root: Path,
        api_key: str,
        invocation: AgentInvocation,
    ) -> None:
        ids = json.loads((run_dir / spec.benchmark.pool_path).read_text())["instance_ids"]
        attempts_root = run_dir / "artifacts" / "retry_attempts" / "harbor"
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
            round_root = attempts_root / f"attempt-{attempt:02d}"
            job_name = f"{spec.run_id}-attempt-{attempt:02d}"
            command = build_command(
                spec,
                run_dir,
                cache_root,
                api_key,
                invocation,
                task_ids=pending,
                output_dir=round_root,
                job_name=job_name,
                engine_managed_retries=True,
            )
            try:
                run_logged(
                    command,
                    cwd=run_dir,
                    log_path=run_dir / "logs" / "execute.log",
                    env=invocation.process_environment,
                    redact_values=[api_key],
                    cost_reader=lambda: collected_cost(attempts_root),
                    budget_usd=spec.budget.total_usd,
                )
            except StageError as error:
                if "budget" in str(error).lower():
                    raise
            by_id: dict[str, tuple[Path, dict[str, object]]] = {}
            for result_path in round_root.glob("*/*/result.json"):
                result = json.loads(result_path.read_text())
                name = result.get("task_name")
                if isinstance(name, str):
                    by_id[name.rsplit("/", 1)[-1]] = (result_path.parent, result)
            for task_id in pending:
                found = by_id.get(task_id)
                if found is None:
                    record_attempt(
                        manifest,
                        task_id,
                        attempt=attempt,
                        artifact=str(round_root.relative_to(run_dir)),
                        status="error",
                        error_type="MissingTrialResult",
                        retryable=True,
                        cost_usd=None,
                    )
                    continue
                trial_dir, result = found
                exception = result.get("exception_info")
                exception = exception if isinstance(exception, dict) else {}
                error_type = exception.get("exception_type")
                message = exception.get("exception_message")
                retryable = is_transient(
                    str(error_type) if error_type else None,
                    str(message) if message else None,
                )
                agent = result.get("agent_result")
                agent = agent if isinstance(agent, dict) else {}
                raw_cost = agent.get("cost_usd")
                cost = float(raw_cost) if isinstance(raw_cost, int | float) else None
                record_attempt(
                    manifest,
                    task_id,
                    attempt=attempt,
                    artifact=str(trial_dir.relative_to(run_dir)),
                    status="error" if error_type else "completed",
                    error_type=str(error_type) if error_type else None,
                    retryable=retryable,
                    cost_usd=cost,
                )
                if not error_type or not retryable:
                    select_attempt(manifest, task_id, attempt)
            save_manifest(run_dir, manifest)

        pending_tasks(manifest, max_attempts)
        save_manifest(run_dir, manifest)
        canonical_job = harbor_job_dir(spec, run_dir)
        canonical_job.mkdir(parents=True, exist_ok=True)
        for task_id in ids:
            task = manifest["tasks"][task_id]
            selected = task["selected_attempt"]
            item = next(entry for entry in task["attempts"] if entry["attempt"] == selected)
            source = run_dir / item["artifact"]
            destination = canonical_job / task_id
            if (source / "result.json").is_file():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "result.json").write_text(
                    json.dumps(
                        {
                            "task_name": f"terminal-bench/{task_id}",
                            "exception_info": {
                                "exception_type": item["error_type"],
                                "exception_message": "retry attempts exhausted without a result",
                            },
                        },
                        indent=2,
                    )
                    + "\n"
                )
