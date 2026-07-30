from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from agent_benchmark.benchmarks.aider_polyglot.pool import catalog, create_pool
from agent_benchmark.benchmarks.aider_polyglot.results import normalize, validate_and_summarize
from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.process import run_logged
from agent_benchmark.run.result import TaskResult


def _checkout(source: Path, url: str, revision: str, run_dir: Path) -> None:
    if not (source / ".git").is_dir():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.mkdir()
        run_logged(
            ["git", "init"],
            cwd=source,
            log_path=run_dir / "logs" / "prepare.log",
        )
        run_logged(
            ["git", "remote", "add", "origin", url],
            cwd=source,
            log_path=run_dir / "logs" / "prepare.log",
        )
    run_logged(
        ["git", "fetch", "--depth", "1", "origin", revision],
        cwd=source,
        log_path=run_dir / "logs" / "prepare.log",
    )
    run_logged(
        ["git", "checkout", "--detach", revision],
        cwd=source,
        log_path=run_dir / "logs" / "prepare.log",
    )


class AiderPolyglot(BenchmarkPlugin):
    name = "aider_polyglot"
    compatible_harnesses = frozenset({"aider-native"})

    def create_pool(self, output_path: Path, sampling: str | None, size: int | None) -> None:
        create_pool(output_path, sampling, size)

    def prepare(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        settings = spec.benchmark.settings
        runner_revision = str(settings["runner_revision"])
        dataset_revision = str(settings["dataset_revision"])
        runner = cache_root / "sources" / f"aider-{runner_revision}"
        dataset = cache_root / "sources" / f"aider-polyglot-{dataset_revision}"
        _checkout(runner, "https://github.com/Aider-AI/aider.git", runner_revision, run_dir)
        _checkout(
            dataset,
            "https://github.com/Aider-AI/polyglot-benchmark.git",
            dataset_revision,
            run_dir,
        )

        dockerfile = runner / "benchmark" / "Dockerfile"
        actual_dockerfile_hash = hashlib.sha256(dockerfile.read_bytes()).hexdigest()
        expected_dockerfile_hash = str(settings["dockerfile_sha256"])
        if actual_dockerfile_hash != expected_dockerfile_hash:
            raise StageError(
                "Aider benchmark Dockerfile hash mismatch: "
                f"expected {expected_dockerfile_hash}, got {actual_dockerfile_hash}"
            )

        image = str(settings["image"])
        run_logged(
            ["docker", "build", "-f", str(dockerfile), "-t", image, "."],
            cwd=runner,
            log_path=run_dir / "logs" / "prepare.log",
        )
        run_logged(
            ["docker", "image", "inspect", image],
            cwd=run_dir,
            log_path=run_dir / "logs" / "prepare.log",
        )

        native = run_dir / "artifacts" / "aider_native"
        exercises = native / "polyglot-benchmark"
        if exercises.exists():
            shutil.rmtree(exercises)
        pool = json.loads((run_dir / spec.benchmark.pool_path).read_text())
        known = {task["id"]: task for task in catalog()["tasks"]}
        for identity in pool["instance_ids"]:
            task = known.get(identity)
            if not task:
                raise StageError(f"unknown Aider Polyglot task: {identity}")
            source = dataset / task["path"]
            destination = exercises / task["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
        (native / "provenance.json").write_text(
            json.dumps(
                {
                    "runner_revision": runner_revision,
                    "dataset_revision": dataset_revision,
                    "image": image,
                    "task_count": len(pool["instance_ids"]),
                },
                indent=2,
            )
            + "\n"
        )

    def grade(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        del cache_root
        validate_and_summarize(spec, run_dir)

    def normalize(self, spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
        return normalize(spec, run_dir)
