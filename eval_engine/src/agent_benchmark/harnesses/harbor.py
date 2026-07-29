from __future__ import annotations

import json
from pathlib import Path

import yaml

from agent_benchmark.benchmarks.paths import benchmark_dataset_dir
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.harnesses.base import HarnessAdapter
from agent_benchmark.run.process import run_logged


def harbor_job_dir(spec: ResolvedSpec, run_dir: Path) -> Path:
    return run_dir / "artifacts" / "harbor_jobs" / spec.run_id


def build_command(
    spec: ResolvedSpec,
    run_dir: Path,
    cache_root: Path,
    api_key: str,
) -> list[str]:
    output_dir = run_dir / "artifacts" / "harbor_jobs"
    job_dir = harbor_job_dir(spec, run_dir)
    if (job_dir / "config.json").is_file():
        return ["uv", "run", "harbor", "job", "resume", "-p", str(job_dir)]

    config_path = run_dir / "subject-config.yaml"
    command = ["uv", "run", "harbor", "run"]
    dataset_source = spec.benchmark.settings.get("dataset_source", "local")
    if dataset_source == "package":
        pool = json.loads((run_dir / spec.benchmark.pool_path).read_text())
        task_name_prefix = str(spec.benchmark.settings.get("task_name_prefix", ""))
        command.extend(["-d", spec.benchmark.dataset_id])
        for task_id in pool["instance_ids"]:
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
            spec.model.model_id,
            "--ak",
            f"config_file={config_path}",
            "--ak",
            f"version={spec.model.subject_agent_version}",
            "--ak",
            f"cost_limit={spec.budget.per_task_usd}",
            "--ae",
            f"{spec.model.api_key_env}={api_key}",
            "-e",
            spec.execution.environment,
            "-n",
            str(spec.execution.workers),
            "-k",
            str(spec.benchmark.settings.get("attempts", 1)),
            "-o",
            str(output_dir),
            "--job-name",
            spec.run_id,
            "--yes",
        ]
    )
    if spec.model.api == "openrouter":
        command.extend(["--ae", f"MSWEA_API_KEY={api_key}"])
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
        config_path = run_dir / "subject-config.yaml"
        config_path.write_text(yaml.safe_dump(spec.model.config, sort_keys=False))

        metadata = {
            "run_id": spec.run_id,
            "benchmark": spec.benchmark.profile,
            "sampling": spec.benchmark.sampling,
            "sample_size": spec.benchmark.sample_size,
            "model_profile": spec.model.profile,
            "model_id": spec.model.model_id,
            "api": spec.model.api,
            "reasoning_effort": spec.model.reasoning_effort,
            "workers": spec.execution.workers,
        }
        (run_dir / "artifacts" / "run_meta.json").write_text(json.dumps(metadata, indent=2) + "\n")

        command = build_command(spec, run_dir, cache_root, api_key)

        command_env = {
            "MSWEA_GLOBAL_COST_LIMIT": str(spec.budget.per_task_usd),
            spec.model.api_key_env: api_key,
        }
        if spec.model.api == "openrouter":
            # Harbor persists this as ${MSWEA_API_KEY}, allowing native resume safely.
            command_env["MSWEA_API_KEY"] = api_key

        run_logged(
            command,
            cwd=run_dir,
            log_path=run_dir / "logs" / "execute.log",
            env=command_env,
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
