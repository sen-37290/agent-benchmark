from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
import yaml
from dotenv import load_dotenv

from agent_benchmark.benchmarks import benchmark_plugin
from agent_benchmark.config.loader import (
    benchmark_plugin_name,
    list_profiles,
    resolve,
    target_profile,
)
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import AgentBenchError
from agent_benchmark.run.pipeline import Pipeline
from agent_benchmark.run.remote import active_run_id
from agent_benchmark.run.result import read_results, write_report, write_results
from agent_benchmark.run.store import RunStore
from agent_benchmark.run.worker import create_manifest, run_stage

app = typer.Typer(
    name="agent-bench",
    help="Deterministic benchmark evaluation engine.",
    no_args_is_help=True,
)
profiles_app = typer.Typer(help="Inspect registered profiles.")
remote_app = typer.Typer(help="Inspect the fixed execution VM.")
app.add_typer(profiles_app, name="profiles")
app.add_typer(remote_app, name="remote")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs"
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _request(
    benchmark: str,
    sampling: str | None,
    size: int | None,
    model: str,
    agent: str | None,
    reasoning_effort: str,
    provider: str,
    provider_route: str | None,
    byok: bool,
    workers: int,
    budget_usd: float,
    per_task_cost_limit_usd: float,
    target: str,
) -> UserRequest:
    return UserRequest(
        benchmark=benchmark,
        sampling=sampling,
        size=size,
        model=model,
        agent=agent,
        reasoning_effort=reasoning_effort,
        provider=provider,
        provider_route=provider_route,
        byok=byok,
        workers=workers,
        budget_usd=budget_usd,
        per_task_cost_limit_usd=per_task_cost_limit_usd,
        target=target,
    )


def _new_run_id(benchmark: str, model: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = f"{benchmark}-{model}".replace("_", "-")
    return f"{timestamp}-{slug}-{uuid.uuid4().hex[:8]}"


def _create_run(request: UserRequest, runs_root: Path, pools_root: Path | None = None) -> RunStore:
    run_id = _new_run_id(request.benchmark, request.model)
    pool_path = (pools_root or (PROJECT_ROOT / "pools")) / f"{run_id}.json"
    benchmark_plugin(benchmark_plugin_name(request.benchmark)).create_pool(
        pool_path, request.sampling, request.size
    )
    try:
        spec = resolve(request, run_id, PROJECT_ROOT, pool_path)
        store = RunStore(runs_root, run_id)
        store.create(request, spec, pool_path)
    except Exception:
        pool_path.unlink(missing_ok=True)
        raise
    return store


@app.command()
def plan(
    benchmark: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    reasoning_effort: Annotated[str, typer.Option()],
    provider: Annotated[str, typer.Option()],
    workers: Annotated[int, typer.Option(min=1)],
    budget_usd: Annotated[float, typer.Option(min=0.01)],
    sampling: Annotated[str | None, typer.Option(help="Benchmark-specific strategy.")] = None,
    size: Annotated[int | None, typer.Option(min=1, help="Number of tasks to sample.")] = None,
    agent: Annotated[
        str | None, typer.Option(help="Agent profile; overrides the benchmark default.")
    ] = None,
    provider_route: Annotated[str | None, typer.Option()] = None,
    byok: Annotated[bool, typer.Option()] = False,
    per_task_cost_limit_usd: Annotated[float, typer.Option(min=0.01)] = 5.0,
    target: Annotated[str, typer.Option()] = "fixed-vm",
) -> None:
    """Validate arguments and print the immutable resolved spec without creating a run."""
    request = _request(
        benchmark,
        sampling,
        size,
        model,
        agent,
        reasoning_effort,
        provider,
        provider_route,
        byok,
        workers,
        budget_usd,
        per_task_cost_limit_usd,
        target,
    )
    run_id = _new_run_id(benchmark, model)
    with tempfile.TemporaryDirectory(prefix="agent-bench-plan-") as temporary:
        pool_path = Path(temporary) / "pool.json"
        benchmark_plugin(benchmark_plugin_name(benchmark)).create_pool(pool_path, sampling, size)
        spec = resolve(request, run_id, PROJECT_ROOT, pool_path)
    typer.echo(yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False))


@app.command()
def run(
    benchmark: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    reasoning_effort: Annotated[str, typer.Option()],
    provider: Annotated[str, typer.Option()],
    workers: Annotated[int, typer.Option(min=1)],
    budget_usd: Annotated[float, typer.Option(min=0.01)],
    sampling: Annotated[str | None, typer.Option(help="Benchmark-specific strategy.")] = None,
    size: Annotated[int | None, typer.Option(min=1, help="Number of tasks to sample.")] = None,
    agent: Annotated[
        str | None, typer.Option(help="Agent profile; overrides the benchmark default.")
    ] = None,
    provider_route: Annotated[str | None, typer.Option()] = None,
    byok: Annotated[bool, typer.Option()] = False,
    per_task_cost_limit_usd: Annotated[float, typer.Option(min=0.01)] = 5.0,
    target: Annotated[str, typer.Option()] = "fixed-vm",
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Create and execute a complete benchmark run."""
    request = _request(
        benchmark,
        sampling,
        size,
        model,
        agent,
        reasoning_effort,
        provider,
        provider_route,
        byok,
        workers,
        budget_usd,
        per_task_cost_limit_usd,
        target,
    )
    store = _create_run(request, runs_root)
    typer.echo(f"created: {store.run_id}")
    typer.echo(f"run ID: {store.run_id}")
    Pipeline(store, PROJECT_ROOT).run()
    typer.echo(f"completed: {store.run_id}")


@app.command()
def resume(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Resume a failed or interrupted run from its first incomplete stage."""
    Pipeline(RunStore(runs_root, run_id), PROJECT_ROOT).run()
    typer.echo(f"completed: {run_id}")


@app.command()
def cancel(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Stop a run, remove its remote resources, and release its VM lease."""
    store = RunStore(runs_root, run_id)
    pipeline = Pipeline(store, PROJECT_ROOT)
    pipeline.backend.cancel()
    store.cancel()
    typer.echo(f"cancelled: {run_id}")


@app.command()
def status(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Show durable stage state for a run."""
    state = RunStore(runs_root, run_id).load_state()
    typer.echo(f"run: {run_id}")
    if state.cancelled_at:
        typer.echo(f"cancelled: {state.cancelled_at.isoformat()}")
    for stage, record in state.stages.items():
        suffix = f" — {record.error}" if record.error else ""
        typer.echo(f"{stage.value:10} {record.status.value:10} attempts={record.attempts}{suffix}")


@app.command()
def active(
    target: Annotated[str, typer.Option()] = "fixed-vm",
) -> None:
    """Show the run ID holding the execution VM lease, if any."""
    target_spec = target_profile(target)
    host = os.environ.get(target_spec.host_env, "").strip()
    if not host:
        raise typer.BadParameter(f"set {target_spec.host_env}")
    run_id = active_run_id(host, target_spec.cache_root)
    typer.echo(run_id if run_id else "no active run")


@app.command()
def fetch(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Re-fetch and verify remote artifacts without deleting them."""
    Pipeline(RunStore(runs_root, run_id), PROJECT_ROOT).collect()
    typer.echo(f"verified artifacts: {run_id}")


@app.command("report")
def report_command(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Regenerate a local report from collected artifacts."""
    store = RunStore(runs_root, run_id)
    spec = store.load_spec()
    path = store.path / "results" / "task_results.jsonl"
    if path.exists():
        summary = write_report(spec, store.path, read_results(store.path))
    else:
        results = benchmark_plugin(spec.benchmark.plugin).normalize(spec, store.path)
        write_results(store.path, results)
        summary = write_report(spec, store.path, results)
    typer.echo(json.dumps(summary, indent=2))


@profiles_app.command("list")
def profiles_list() -> None:
    """List profiles accepted by the public CLI."""
    for group, names in list_profiles().items():
        typer.echo(f"{group}:")
        for name in names:
            typer.echo(f"  - {name}")


@remote_app.command("doctor")
def remote_doctor(
    target: Annotated[str, typer.Option()] = "fixed-vm",
) -> None:
    """Check SSH connectivity and required VM executables without changing the VM."""
    del target  # v1 has one SSH target; target profiles remain the extension point.
    host = os.environ.get("AGENT_BENCH_SSH_HOST", "").strip()
    if not host:
        raise typer.BadParameter("set AGENT_BENCH_SSH_HOST")
    checks = (
        "command -v python3 && command -v uv && command -v docker && command -v rsync "
        "&& docker compose version >/dev/null"
    )
    result = subprocess.run(["ssh", host, checks], check=False)
    if result.returncode:
        raise typer.Exit(result.returncode)
    typer.echo("remote preflight passed")


@app.command(hidden=True)
def worker(
    stage: str,
    run_dir: Annotated[Path, typer.Option()],
    spec: Annotated[Path | None, typer.Option()] = None,
    cache_root: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Internal entrypoint invoked on the remote VM."""
    if stage == "manifest":
        create_manifest(run_dir)
        return
    if spec is None or cache_root is None:
        raise typer.BadParameter("--spec and --cache-root are required for worker stages")
    run_stage(stage, spec, run_dir, cache_root)


def main() -> None:
    try:
        app()
    except AgentBenchError as error:
        typer.echo(f"error: {error}", err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
