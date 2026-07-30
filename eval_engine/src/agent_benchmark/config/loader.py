from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from agent_benchmark import __version__
from agent_benchmark.config.schema import (
    BenchmarkSpec,
    BudgetSpec,
    ExecutionSpec,
    ModelSpec,
    ProvenanceSpec,
    ResolvedSpec,
    TargetSpec,
    UserRequest,
)
from agent_benchmark.exceptions import ConfigurationError

CONFIG_ROOT = files("agent_benchmark.config")


def _load_yaml(group: str, name: str) -> dict[str, Any]:
    path = CONFIG_ROOT.joinpath(group, f"{name}.yaml")
    if not path.is_file():
        available = sorted(
            p.name.removesuffix(".yaml") for p in CONFIG_ROOT.joinpath(group).iterdir()
        )
        raise ConfigurationError(
            f"unknown {group.rstrip('s')} profile {name!r}; available: {', '.join(available)}"
        )
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ConfigurationError(f"invalid profile: {path}")
    return data


def list_profiles() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for group in ("agents", "benchmarks", "models", "targets"):
        result[group] = sorted(
            p.name.removesuffix(".yaml")
            for p in CONFIG_ROOT.joinpath(group).iterdir()
            if p.name.endswith(".yaml")
        )
    return result


def benchmark_plugin_name(profile: str) -> str:
    return str(_load_yaml("benchmarks", profile)["plugin"])


def _set_dotted(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            raise ConfigurationError(f"cannot set {path!r}: {part!r} is not a mapping")
        cursor = child
    cursor[parts[-1]] = value


def _provenance(project_root: Path) -> ProvenanceSpec:
    lock = project_root / "uv.lock"
    lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None
    revision = None
    dirty = False
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        pass
    return ProvenanceSpec(
        engine_version=__version__,
        source_revision=revision,
        source_dirty=dirty,
        lock_sha256=lock_hash,
    )


def resolve(
    request: UserRequest, run_id: str, project_root: Path, generated_pool: Path
) -> ResolvedSpec:
    benchmark = _load_yaml("benchmarks", request.benchmark)
    model = _load_yaml("models", request.model)
    target = _load_yaml("targets", request.target)
    agent_profile = request.agent or benchmark.get("default_agent")
    if not agent_profile:
        raise ConfigurationError(
            f"benchmark profile {request.benchmark!r} does not define default_agent; "
            "select one with --agent"
        )
    agent = _load_yaml("agents", agent_profile)
    compatible_harnesses = set(agent.get("compatible_harnesses", []))
    if benchmark["harness"] not in compatible_harnesses:
        available = ", ".join(sorted(compatible_harnesses)) or "none"
        raise ConfigurationError(
            f"agent {agent_profile!r} is not compatible with benchmark "
            f"{request.benchmark!r} (required harness {benchmark['harness']!r}; "
            f"agent supports: {available})"
        )

    provider_contracts = {
        "openrouter": ("openrouter", None),
        "friendli": ("openrouter", "friendli"),
        "anthropic": ("anthropic", None),
    }
    supported_providers = set(model.get("supported_providers", [model["api"]]))
    if request.provider not in supported_providers:
        raise ConfigurationError(
            f"model {request.model!r} does not support provider {request.provider!r}; "
            f"available: {', '.join(sorted(supported_providers))}"
        )
    requested_api, default_route = provider_contracts[request.provider]
    if requested_api != model["api"]:
        raise ConfigurationError(
            f"model {request.model!r} uses {model['api']!r} transport and cannot use "
            f"provider {request.provider!r}"
        )
    efforts = model.get("supported_efforts", [])
    if efforts and request.reasoning_effort not in efforts:
        raise ConfigurationError(
            f"effort {request.reasoning_effort!r} is invalid for {request.model!r}; "
            f"available: {', '.join(efforts)}"
        )
    provider_route = request.provider_route or default_route
    if (provider_route or request.byok) and requested_api != "openrouter":
        raise ConfigurationError("--provider-route/--byok are supported only for OpenRouter")

    if not generated_pool.is_file():
        raise ConfigurationError(f"generated pool does not exist: {generated_pool}")
    pool_data = yaml.safe_load(generated_pool.read_text())
    instance_ids = pool_data.get("instance_ids") if isinstance(pool_data, dict) else None
    if not isinstance(instance_ids, list) or not instance_ids:
        raise ConfigurationError("generated pool must contain a non-empty instance_ids list")
    if len(instance_ids) != len(set(instance_ids)):
        raise ConfigurationError("generated pool contains duplicate instance IDs")

    config = copy.deepcopy(model["config"])
    _set_dotted(config, model["effort_path"], request.reasoning_effort)
    if provider_route or request.byok:
        provider_config = (
            config.setdefault("model", {}).setdefault("model_kwargs", {}).setdefault("provider", {})
        )
        if provider_route:
            provider_config["only"] = [provider_route]
        if request.byok:
            # NOTE: OpenRouter ignores a Friendli key sent with inference requests and uses the
            # Friendli BYOK key already registered in the OpenRouter dashboard.
            provider_config["allow_fallbacks"] = False

    return ResolvedSpec(
        run_id=run_id,
        benchmark=BenchmarkSpec(
            profile=request.benchmark,
            plugin=benchmark["plugin"],
            harness=benchmark["harness"],
            dataset_id=benchmark["dataset_id"],
            sampling=request.sampling or "full",
            sample_size=len(instance_ids),
            pool_path="inputs/pool.json",
            pool_sha256=hashlib.sha256(generated_pool.read_bytes()).hexdigest(),
            grading=benchmark["grading"],
            settings=benchmark.get("settings", {}),
        ),
        model=ModelSpec(
            profile=request.model,
            model_id=model["model_id"],
            subject_agent=agent["name"],
            subject_agent_version=str(agent["version"]),
            model_class=model["model_class"],
            provider=request.provider,
            api=model["api"],
            api_key_env=model["api_key_env"],
            effort_path=model["effort_path"],
            reasoning_effort=request.reasoning_effort,
            provider_route=provider_route,
            byok=request.byok,
            config=config,
        ),
        target=TargetSpec(profile=request.target, **target),
        execution=ExecutionSpec(workers=request.workers),
        budget=BudgetSpec(
            total_usd=request.budget_usd,
            per_task_usd=request.per_task_cost_limit_usd,
        ),
        provenance=_provenance(project_root),
    )


def canonical_json(spec: ResolvedSpec) -> str:
    return json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
