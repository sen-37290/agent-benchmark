from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from agent_benchmark.benchmarks import benchmark_plugin
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.harnesses import harness_adapter


def load_spec(path: Path) -> ResolvedSpec:
    return ResolvedSpec.model_validate(yaml.safe_load(path.read_text()))


def run_stage(stage: str, spec_path: Path, run_dir: Path, cache_root: Path) -> None:
    spec = load_spec(spec_path)
    benchmark = benchmark_plugin(spec.benchmark.plugin)
    if spec.benchmark.harness not in benchmark.compatible_harnesses:
        raise ConfigurationError(
            f"{spec.benchmark.plugin} is not compatible with {spec.benchmark.harness}"
        )
    if stage == "prepare":
        benchmark.prepare(spec, run_dir, cache_root)
    elif stage == "execute":
        secret_path = run_dir / "secrets.json"
        secrets = json.loads(secret_path.read_text())
        harness_adapter(spec.benchmark.harness).execute(spec, run_dir, cache_root, secrets)
    elif stage == "grade":
        benchmark.grade(spec, run_dir, cache_root)
    else:
        raise ConfigurationError(f"unsupported remote worker stage: {stage}")


def create_manifest(run_dir: Path) -> Path:
    entries: dict[str, dict[str, str | int]] = {}
    for directory in (run_dir / "artifacts", run_dir / "logs"):
        if not directory.exists():
            continue
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            relative = str(path.relative_to(run_dir))
            entries[relative] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
    destination = run_dir / "artifact-manifest.json"
    destination.write_text(json.dumps({"version": 1, "files": entries}, indent=2) + "\n")
    return destination
