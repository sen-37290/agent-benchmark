from __future__ import annotations

import json
import shutil
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.benchmarks.paths import benchmark_dataset_dir
from agent_benchmark.benchmarks.swebench_verified.pool import create_pool
from agent_benchmark.benchmarks.swebench_verified.results import (
    instance_id,
    normalize,
    pool_ids,
    trial_results,
)
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.process import run_logged
from agent_benchmark.run.result import TaskResult

TEMPLATE_REQUIRED_FILES = (
    "task.toml",
    "instruction.md",
    "environment/Dockerfile",
    "solution/solve.sh",
    "tests/test.sh",
)
PREPARED_TASK_REQUIRED_FILES = (
    "task.toml",
    "instruction.md",
    "environment/Dockerfile",
    "tests/test.sh",
)
HARBOR_TEMPLATE_VERSION = "2"


def _incomplete_prepared_tasks(dataset_dir: Path, ids: list[str]) -> list[str]:
    return [
        task_id
        for task_id in ids
        if any(
            not (dataset_dir / task_id / relative).is_file()
            for relative in PREPARED_TASK_REQUIRED_FILES
        )
    ]


class SwebenchVerified(BenchmarkPlugin):
    name = "swebench_verified"
    compatible_harnesses = frozenset({"harbor"})

    def create_pool(self, output_path: Path, sampling: str | None, size: int | None) -> None:
        create_pool(output_path, sampling, size)

    def prepare(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        adapter_ref = str(spec.benchmark.settings["harbor_adapter_ref"])
        adapter_swebench = str(spec.benchmark.settings["adapter_swebench_version"])
        dataset_dir = benchmark_dataset_dir(spec, cache_root)
        ids = pool_ids(spec, run_dir)
        complete = dataset_dir / ".agent-bench-complete.json"
        if complete.exists():
            marker = json.loads(complete.read_text())
            expected = {
                "pool_sha256": spec.benchmark.pool_sha256,
                "adapter_ref": adapter_ref,
                "adapter_swebench_version": adapter_swebench,
                "harbor_template_version": HARBOR_TEMPLATE_VERSION,
            }
            if all(marker.get(key) == value for key, value in expected.items()) and not (
                _incomplete_prepared_tasks(dataset_dir, ids)
            ):
                return

        adapter_root = cache_root / "sources" / f"harbor-{adapter_ref}"
        if not (adapter_root / ".git").is_dir():
            adapter_root.parent.mkdir(parents=True, exist_ok=True)
            run_logged(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    adapter_ref,
                    "https://github.com/harbor-framework/harbor.git",
                    str(adapter_root),
                ],
                cwd=run_dir,
                log_path=run_dir / "logs" / "prepare.log",
            )

        dataset_dir.mkdir(parents=True, exist_ok=True)
        package_files = files("agent_benchmark.benchmarks.swebench_verified")
        template = Path(str(package_files.joinpath("assets")))
        missing_template_files = [
            relative for relative in TEMPLATE_REQUIRED_FILES if not (template / relative).is_file()
        ]
        if missing_template_files:
            missing = ", ".join(missing_template_files)
            raise StageError(f"incomplete SWE-bench template; missing: {missing}")
        command = [
            "uv",
            "run",
            "--with",
            f"swebench=={adapter_swebench}",
            "swebench",
            "--no-all",
            "--task-ids",
            *ids,
            "--template-dir",
            str(template),
            "--task-dir",
            str(dataset_dir),
            "--overwrite",
        ]
        run_logged(
            command,
            cwd=adapter_root / "adapters" / "swebench",
            log_path=run_dir / "logs" / "prepare.log",
        )
        generated = [child for child in dataset_dir.iterdir() if child.is_dir()]
        incomplete = _incomplete_prepared_tasks(dataset_dir, ids)
        if len(generated) != len(ids) or incomplete:
            detail = f"prepared {len(generated)} tasks, expected {len(ids)}"
            if incomplete:
                detail += f"; incomplete tasks: {', '.join(sorted(incomplete))}"
            raise StageError(detail)
        complete.write_text(
            json.dumps(
                {
                    "pool_sha256": spec.benchmark.pool_sha256,
                    "adapter_ref": adapter_ref,
                    "adapter_swebench_version": adapter_swebench,
                    "harbor_template_version": HARBOR_TEMPLATE_VERSION,
                    "task_count": len(generated),
                },
                indent=2,
            )
            + "\n"
        )

    def grade(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        del cache_root
        expected_grader = str(spec.benchmark.settings["official_grader_version"])
        actual_grader = version("swebench")
        if actual_grader != expected_grader:
            raise StageError(
                f"official grader version mismatch: expected {expected_grader}, got {actual_grader}"
            )
        ids = set(pool_ids(spec, run_dir))
        jobs = run_dir / "artifacts" / "harbor_jobs"
        predictions: dict[str, dict[str, str]] = {}
        for result_path in trial_results(jobs):
            result = json.loads(result_path.read_text())
            trial_dir = result_path.parent
            task_id = instance_id(trial_dir, result, ids)
            if not task_id:
                continue
            patch_path = trial_dir / "verifier" / "model_patch.diff"
            predictions[task_id] = {
                "instance_id": task_id,
                "model_name_or_path": spec.model.model_id,
                "model_patch": patch_path.read_text() if patch_path.exists() else "",
            }
        if not predictions:
            raise StageError("no SWE-bench predictions could be extracted from Harbor output")

        predictions_path = run_dir / "artifacts" / "predictions.json"
        predictions_path.write_text(json.dumps(predictions, indent=2) + "\n")
        grade_id = f"agent_bench_{spec.run_id}"
        command = [
            "uv",
            "run",
            "python",
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            spec.benchmark.dataset_id,
            "--split",
            "test",
            "--instance_ids",
            *predictions,
            "--predictions_path",
            str(predictions_path),
            "--max_workers",
            str(max(1, min(spec.execution.workers, 8))),
            "--run_id",
            grade_id,
        ]
        run_logged(command, cwd=run_dir, log_path=run_dir / "logs" / "grade.log")
        summaries = sorted(run_dir.glob(f"*.{grade_id}.json"), key=lambda p: p.stat().st_mtime)
        if not summaries:
            raise StageError("official SWE-bench grader did not produce a summary")
        shutil.move(summaries[-1], run_dir / "artifacts" / "official_summary.json")

    def normalize(self, spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
        return normalize(spec, run_dir)
