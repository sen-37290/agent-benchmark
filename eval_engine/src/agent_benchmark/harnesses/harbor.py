from __future__ import annotations

import json
from pathlib import Path

import yaml

from agent_benchmark.errors import ConfigurationError, StageError
from agent_benchmark.harnesses.base import HarnessAdapter
from agent_benchmark.models import ResolvedSpec
from agent_benchmark.paths import benchmark_dataset_dir
from agent_benchmark.process import run_logged


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

        dataset_dir = benchmark_dataset_dir(spec, cache_root)
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

        command = [
            "uv",
            "run",
            "harbor",
            "run",
            "-p",
            str(dataset_dir),
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
            "-o",
            str(output_dir),
            "--yes",
        ]
        if spec.model.api == "openrouter":
            # Required by Harbor for unprefixed native OpenRouter model identifiers.
            command.extend(["--ae", f"MSWEA_API_KEY={api_key}"])

        run_logged(
            command,
            cwd=run_dir,
            log_path=run_dir / "logs" / "execute.log",
            env={"MSWEA_GLOBAL_COST_LIMIT": str(spec.budget.per_task_usd)},
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
        if exceptions:
            raise StageError(
                f"Harbor reported task exceptions for {len(exceptions)}/{len(trial_results)} "
                f"trials; inspect logs/execute.log (examples: {', '.join(exceptions[:3])})"
            )
