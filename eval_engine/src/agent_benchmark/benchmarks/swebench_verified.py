from __future__ import annotations

import json
import random
import re
import shutil
from collections import defaultdict
from datetime import datetime
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import Any

from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.errors import StageError
from agent_benchmark.models import ResolvedSpec, TaskResult
from agent_benchmark.paths import benchmark_dataset_dir
from agent_benchmark.process import run_logged

INSTANCE_RE = re.compile(r"[A-Za-z0-9][\w.-]*__[\w.-]+-\d+")
DATASET_SIZE = 500
SAMPLING_SEED = 42
PREFIX_TO_DOMAIN = {
    "sympy": "Symbolic math",
    "sphinx-doc": "Dev tooling",
    "pytest-dev": "Dev tooling",
    "pylint-dev": "Dev tooling",
    "scikit-learn": "Scientific & data",
    "astropy": "Scientific & data",
    "pydata": "Scientific & data",
    "matplotlib": "Data-viz",
    "mwaskom": "Data-viz",
    "django": "Web & HTTP",
    "psf": "Web & HTTP",
    "pallets": "Web & HTTP",
}


def _domain_of(instance_id: str) -> str:
    prefix = instance_id.split("__", 1)[0]
    try:
        return PREFIX_TO_DOMAIN[prefix]
    except KeyError as error:
        raise StageError(f"unknown SWE-bench repository prefix: {prefix}") from error


def _pool_ids(spec: ResolvedSpec, run_dir: Path) -> list[str]:
    return json.loads((run_dir / spec.benchmark.pool_path).read_text())["instance_ids"]


def _trial_results(job_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(job_dir.glob("**/result.json"))
        if (path.parent / "agent").is_dir() or (path.parent / "verifier").is_dir()
    ]


def _instance_id(trial_dir: Path, result: dict[str, Any], valid_ids: set[str]) -> str | None:
    probes = [result.get("task_name"), result.get("trial_name"), str(trial_dir)]
    task_id = result.get("task_id") or {}
    if isinstance(task_id, dict):
        probes.insert(0, task_id.get("path"))
    for probe in probes:
        match = INSTANCE_RE.search(str(probe or ""))
        if match and match.group(0) in valid_ids:
            return match.group(0)
    return None


def _cost_and_tokens(
    result: dict[str, Any],
) -> tuple[float | None, int | None, int | None, int | None]:
    contexts: list[dict[str, Any]] = []
    if result.get("agent_result"):
        contexts.append(result["agent_result"])
    else:
        contexts.extend(
            step["agent_result"]
            for step in result.get("step_results", [])
            if step.get("agent_result")
        )
    cost: float | None = None
    input_tokens = output_tokens = cached_tokens = None
    for context in contexts:
        if context.get("cost_usd") is not None:
            cost = (cost or 0.0) + float(context["cost_usd"])
        if context.get("n_input_tokens") is not None:
            input_tokens = (input_tokens or 0) + int(context["n_input_tokens"])
        if context.get("n_output_tokens") is not None:
            output_tokens = (output_tokens or 0) + int(context["n_output_tokens"])
        if context.get("n_cache_tokens") is not None:
            cached_tokens = (cached_tokens or 0) + int(context["n_cache_tokens"])
    return cost, input_tokens, output_tokens, cached_tokens


def _duration(result: dict[str, Any]) -> float | None:
    try:
        return (
            datetime.fromisoformat(result["finished_at"])
            - datetime.fromisoformat(result["started_at"])
        ).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


class SwebenchVerified(BenchmarkPlugin):
    name = "swebench_verified"
    compatible_harnesses = frozenset({"harbor"})

    def create_pool(self, output_path: Path, sampling: str | None, size: int | None) -> None:
        try:
            from datasets import load_dataset
        except ImportError as error:
            raise StageError(
                "SWE-bench pool generation requires `uv sync --extra swebench`"
            ) from error

        dataset = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
        all_ids = sorted(str(row["instance_id"]) for row in dataset)
        if len(all_ids) != DATASET_SIZE:
            raise StageError(f"expected {DATASET_SIZE} Verified tasks, got {len(all_ids)}")

        if sampling is None and size is None:
            strategy = "full"
            selected = all_ids
        else:
            assert sampling is not None and size is not None
            if sampling not in {"random", "domain"}:
                raise StageError("SWE-bench sampling must be 'random' or 'domain'")
            if not 1 <= size <= DATASET_SIZE:
                raise StageError(f"SWE-bench size must be between 1 and {DATASET_SIZE}")
            strategy = sampling
            selected = self._sample(all_ids, strategy, size)

        by_domain: dict[str, list[str]] = defaultdict(list)
        for instance_id in selected:
            by_domain[_domain_of(instance_id)].append(instance_id)
        payload = {
            "benchmark": self.name,
            "dataset_id": "princeton-nlp/SWE-bench_Verified",
            "sampling": strategy,
            "seed": SAMPLING_SEED if strategy != "full" else None,
            "n": len(selected),
            "instance_ids": sorted(selected),
            "by_domain": dict(sorted(by_domain.items())),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2) + "\n")

    @staticmethod
    def _sample(all_ids: list[str], strategy: str, size: int) -> list[str]:
        rng = random.Random(SAMPLING_SEED)
        if size == len(all_ids):
            return all_ids
        if strategy == "random":
            return sorted(rng.sample(all_ids, size))

        grouped: dict[str, list[str]] = defaultdict(list)
        for instance_id in all_ids:
            grouped[_domain_of(instance_id)].append(instance_id)
        queues = {
            domain: rng.sample(items, len(items)) for domain, items in sorted(grouped.items())
        }
        selected: list[str] = []
        while len(selected) < size:
            for domain in sorted(queues):
                if queues[domain] and len(selected) < size:
                    selected.append(queues[domain].pop())
        return sorted(selected)

    def prepare(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        adapter_ref = str(spec.benchmark.settings["harbor_adapter_ref"])
        adapter_swebench = str(spec.benchmark.settings["adapter_swebench_version"])
        dataset_dir = benchmark_dataset_dir(spec, cache_root)
        complete = dataset_dir / ".agent-bench-complete.json"
        if complete.exists():
            marker = json.loads(complete.read_text())
            expected = {
                "pool_sha256": spec.benchmark.pool_sha256,
                "adapter_ref": adapter_ref,
                "adapter_swebench_version": adapter_swebench,
            }
            if all(marker.get(key) == value for key, value in expected.items()):
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

        ids = _pool_ids(spec, run_dir)
        dataset_dir.mkdir(parents=True, exist_ok=True)
        template = Path(str(files("agent_benchmark").joinpath("assets", "swebench_template")))
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
        generated = sum(1 for child in dataset_dir.iterdir() if child.is_dir())
        if generated != len(ids):
            raise StageError(f"prepared {generated} tasks, expected {len(ids)}")
        complete.write_text(
            json.dumps(
                {
                    "pool_sha256": spec.benchmark.pool_sha256,
                    "adapter_ref": adapter_ref,
                    "adapter_swebench_version": adapter_swebench,
                    "task_count": generated,
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
        ids = set(_pool_ids(spec, run_dir))
        jobs = run_dir / "artifacts" / "harbor_jobs"
        predictions: dict[str, dict[str, str]] = {}
        for result_path in _trial_results(jobs):
            result = json.loads(result_path.read_text())
            trial_dir = result_path.parent
            instance_id = _instance_id(trial_dir, result, ids)
            if not instance_id:
                continue
            patch_path = trial_dir / "verifier" / "model_patch.diff"
            predictions[instance_id] = {
                "instance_id": instance_id,
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
        run_logged(
            command,
            cwd=run_dir,
            log_path=run_dir / "logs" / "grade.log",
        )
        summaries = sorted(run_dir.glob(f"*.{grade_id}.json"), key=lambda p: p.stat().st_mtime)
        if not summaries:
            raise StageError("official SWE-bench grader did not produce a summary")
        shutil.move(summaries[-1], run_dir / "artifacts" / "official_summary.json")

    def normalize(self, spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
        ids = _pool_ids(spec, run_dir)
        valid_ids = set(ids)
        summary_path = run_dir / "artifacts" / "official_summary.json"
        if not summary_path.exists():
            raise StageError("official_summary.json is missing")
        summary = json.loads(summary_path.read_text())
        resolved = set(summary.get("resolved_ids") or summary.get("resolved_instances_ids") or [])

        by_id: dict[str, TaskResult] = {}
        jobs = run_dir / "artifacts" / "harbor_jobs"
        for result_path in _trial_results(jobs):
            result = json.loads(result_path.read_text())
            instance_id = _instance_id(result_path.parent, result, valid_ids)
            if not instance_id:
                continue
            cost, input_tokens, output_tokens, cached_tokens = _cost_and_tokens(result)
            exception = result.get("exception_info") or {}
            error_type = exception.get("exception_type")
            by_id[instance_id] = TaskResult(
                run_id=spec.run_id,
                task_id=instance_id,
                status="error" if error_type else "completed",
                metrics={"resolved": instance_id in resolved},
                cost_usd=cost,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                duration_seconds=_duration(result),
                error_type=error_type,
                raw_artifacts=[str(result_path.relative_to(run_dir))],
            )
        return [
            by_id.get(instance_id)
            or TaskResult(
                run_id=spec.run_id,
                task_id=instance_id,
                status="missing",
                metrics={"resolved": False},
                error_type="MissingTrial",
            )
            for instance_id in ids
        ]
