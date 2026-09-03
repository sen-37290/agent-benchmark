from __future__ import annotations

import contextlib
import json
import signal
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
from agent_benchmark.run.remote import SSHBackend
from agent_benchmark.run.result import read_results, write_report, write_results
from agent_benchmark.run.snapshot import build as build_snapshot
from agent_benchmark.run.stop import REASON_OPERATOR, request_stop
from agent_benchmark.run.store import RunStore
from agent_benchmark.run.worker import create_manifest, run_stage

app = typer.Typer(
    name="agent-bench",
    help="Deterministic benchmark evaluation engine.",
    no_args_is_help=True,
)
profiles_app = typer.Typer(help="Inspect registered profiles.")
remote_app = typer.Typer(help="Inspect a configured execution VM.")
app.add_typer(profiles_app, name="profiles")
app.add_typer(remote_app, name="remote")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs"
# The repository-root file is the canonical shared environment for benchmark jobs. Keep the
# engine-local file as a backwards-compatible fallback, without overriding process/root values.
load_dotenv(PROJECT_ROOT.parent / ".env", override=False)
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _request(
    benchmark: str,
    sampling: str | None,
    size: int | None,
    model: str,
    agent: str | None,
    reasoning_effort: str | None,
    provider: str,
    provider_route: str | None,
    byok: bool,
    workers: int,
    budget_usd: float | None,
    no_budget_limit: bool,
    per_task_cost_limit_usd: float | None,
    no_timeout: bool,
    error_retries: int | None,
    target: str,
    label: str | None = None,
    api_key_from: str | None = None,
    no_cleanup: bool = False,
) -> UserRequest:
    return UserRequest(
        label=label,
        api_key_from=api_key_from,
        no_cleanup=no_cleanup,
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
        no_budget_limit=no_budget_limit,
        per_task_cost_limit_usd=per_task_cost_limit_usd,
        no_timeout=no_timeout,
        error_retries=error_retries,
        target=target,
    )


def _install_stop_handlers(run_dir: Path) -> None:
    """Turn SIGTERM/SIGINT into a cooperative stop rather than an abrupt death.

    `systemctl stop` sends SIGTERM and Ctrl-C sends SIGINT. Neither is caught by the pipeline's
    `except Exception`, so before this the process simply vanished: `state.json` was left stuck
    at `running` and in-flight work was abandoned. Recording a stop lets the harnesses drain and
    the controller's exit trap finalize.
    """

    def handle(signum: int, _frame: object) -> None:
        name = signal.Signals(signum).name
        request_stop(run_dir, REASON_OPERATOR, f"received {name}")
        typer.echo(f"\n{name} received: winding down, no new tasks will start", err=True)

    for signum in (signal.SIGTERM, signal.SIGINT):
        # Signals can only be installed on the main thread; ignore it elsewhere (e.g. tests).
        with contextlib.suppress(ValueError):
            signal.signal(signum, handle)


def _selected_target(primary: bool, backup: str | None) -> str:
    if primary and backup is not None:
        raise typer.BadParameter("use either --primary or --backup NAME, not both")
    name = "primary" if backup is None else backup.strip()
    if not name:
        raise typer.BadParameter("--backup requires a non-empty target name")
    target = target_profile(name)
    expected_mode = "primary" if backup is None else "backup"
    if target.mode != expected_mode:
        option = "--primary" if expected_mode == "primary" else "--backup"
        raise typer.BadParameter(
            f"target {name!r} has mode {target.mode!r} and cannot be selected with {option}"
        )
    return name


def _new_run_id(benchmark: str, model: str, label: str | None = None) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = label.strip() if label else f"{benchmark}-{model}"
    slug = slug.replace("_", "-")
    return f"{timestamp}-{slug}-{uuid.uuid4().hex[:8]}"


def _create_run(request: UserRequest, runs_root: Path, pools_root: Path | None = None) -> RunStore:
    run_id = _new_run_id(request.benchmark, request.model, request.label)
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
    provider: Annotated[str, typer.Option()],
    workers: Annotated[int, typer.Option(min=1)],
    budget_usd: Annotated[float | None, typer.Option(min=0.01)] = None,
    no_budget_limit: Annotated[
        bool, typer.Option("--no-budget-limit", help="Disable the run-wide cost watchdog.")
    ] = False,
    reasoning_effort: Annotated[
        str | None, typer.Option(help="Optional model reasoning effort; omit for provider default.")
    ] = None,
    sampling: Annotated[str | None, typer.Option(help="Benchmark-specific strategy.")] = None,
    size: Annotated[int | None, typer.Option(min=1, help="Number of tasks to sample.")] = None,
    agent: Annotated[
        str | None, typer.Option(help="Agent profile; overrides the benchmark default.")
    ] = None,
    provider_route: Annotated[str | None, typer.Option()] = None,
    byok: Annotated[bool, typer.Option()] = False,
    per_task_cost_limit_usd: Annotated[
        float | None,
        typer.Option(min=0.01, help="Per-task limit; defaults to the benchmark profile value."),
    ] = None,
    no_timeout: Annotated[
        bool,
        typer.Option(
            "--no-timeout",
            help="Disable the Harbor agent-execution deadline.",
        ),
    ] = False,
    error_retries: Annotated[
        int | None,
        typer.Option(
            "--error-retries",
            min=0,
            help=(
                "Retry tasks after transient provider failures; reward-0 results are not retried."
            ),
        ),
    ] = None,
    primary: Annotated[
        bool, typer.Option("--primary", help="Use the primary execution VM (the default).")
    ] = False,
    backup: Annotated[
        str | None, typer.Option("--backup", metavar="NAME", help="Use a named backup VM.")
    ] = None,
    label: Annotated[
        str | None,
        typer.Option(
            "--label",
            metavar="NAME",
            help=(
                "Experiment name; prefixes the run ID and identifies the run to the fleet monitor."
            ),
        ),
    ] = None,
    api_key_from: Annotated[
        str | None,
        typer.Option(
            "--api-key-from",
            metavar="ENVVAR",
            help=(
                "Read this experiment's API key from ENVVAR instead of the model profile's "
                "default variable. The subject agent still receives the canonical provider name."
            ),
        ),
    ] = None,
    no_cleanup: Annotated[
        bool,
        typer.Option(
            "--no-cleanup",
            help="Retain the remote run workspace instead of removing it after a successful run.",
        ),
    ] = False,
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
        no_budget_limit,
        per_task_cost_limit_usd,
        no_timeout,
        error_retries,
        _selected_target(primary, backup),
        label=label,
        api_key_from=api_key_from,
        no_cleanup=no_cleanup,
    )
    run_id = _new_run_id(benchmark, model, label)
    with tempfile.TemporaryDirectory(prefix="agent-bench-plan-") as temporary:
        pool_path = Path(temporary) / "pool.json"
        benchmark_plugin(benchmark_plugin_name(benchmark)).create_pool(pool_path, sampling, size)
        spec = resolve(request, run_id, PROJECT_ROOT, pool_path)
    typer.echo(yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False))


@app.command()
def run(
    benchmark: Annotated[str, typer.Option()],
    model: Annotated[str, typer.Option()],
    provider: Annotated[str, typer.Option()],
    workers: Annotated[int, typer.Option(min=1)],
    budget_usd: Annotated[float | None, typer.Option(min=0.01)] = None,
    no_budget_limit: Annotated[
        bool, typer.Option("--no-budget-limit", help="Disable the run-wide cost watchdog.")
    ] = False,
    reasoning_effort: Annotated[
        str | None, typer.Option(help="Optional model reasoning effort; omit for provider default.")
    ] = None,
    sampling: Annotated[str | None, typer.Option(help="Benchmark-specific strategy.")] = None,
    size: Annotated[int | None, typer.Option(min=1, help="Number of tasks to sample.")] = None,
    agent: Annotated[
        str | None, typer.Option(help="Agent profile; overrides the benchmark default.")
    ] = None,
    provider_route: Annotated[str | None, typer.Option()] = None,
    byok: Annotated[bool, typer.Option()] = False,
    per_task_cost_limit_usd: Annotated[
        float | None,
        typer.Option(min=0.01, help="Per-task limit; defaults to the benchmark profile value."),
    ] = None,
    no_timeout: Annotated[
        bool,
        typer.Option(
            "--no-timeout",
            help="Disable the Harbor agent-execution deadline.",
        ),
    ] = False,
    error_retries: Annotated[
        int | None,
        typer.Option(
            "--error-retries",
            min=0,
            help=(
                "Retry tasks after transient provider failures; reward-0 results are not retried."
            ),
        ),
    ] = None,
    primary: Annotated[
        bool, typer.Option("--primary", help="Use the primary execution VM (the default).")
    ] = False,
    backup: Annotated[
        str | None, typer.Option("--backup", metavar="NAME", help="Use a named backup VM.")
    ] = None,
    label: Annotated[
        str | None,
        typer.Option(
            "--label",
            metavar="NAME",
            help=(
                "Experiment name; prefixes the run ID and identifies the run to the fleet monitor."
            ),
        ),
    ] = None,
    api_key_from: Annotated[
        str | None,
        typer.Option(
            "--api-key-from",
            metavar="ENVVAR",
            help=(
                "Read this experiment's API key from ENVVAR instead of the model profile's "
                "default variable. The subject agent still receives the canonical provider name."
            ),
        ),
    ] = None,
    no_cleanup: Annotated[
        bool,
        typer.Option(
            "--no-cleanup",
            help="Retain the remote run workspace instead of removing it after a successful run.",
        ),
    ] = False,
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
        no_budget_limit,
        per_task_cost_limit_usd,
        no_timeout,
        error_retries,
        _selected_target(primary, backup),
        label=label,
        api_key_from=api_key_from,
        no_cleanup=no_cleanup,
    )
    store = _create_run(request, runs_root)
    typer.echo(f"created: {store.run_id}")
    typer.echo(f"run ID: {store.run_id}")
    _install_stop_handlers(store.path)
    Pipeline(store, PROJECT_ROOT).run()
    typer.echo(f"completed: {store.run_id}")


@app.command()
def snapshot(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Print a small JSON progress/cost summary for one run.

    Designed to be cheap enough for the fleet monitor to poll over SSH: it derives task counts
    and spend from the artifacts the harnesses already write, so it stays accurate during the
    execute stage where `state.json` alone only says "running".
    """
    typer.echo(build_snapshot(RunStore(runs_root, run_id)).to_json())


@app.command()
def finalize(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Grade, collect, normalize and report whatever finished, however the run ended.

    Use this after a run is stopped at its cost cap, interrupted, or killed. It never cleans up
    and never deletes anything; it only turns the artifacts already on disk into graded results
    and a report.
    """
    store = RunStore(runs_root, run_id)
    Pipeline(store, PROJECT_ROOT).finalize()
    typer.echo(f"finalized: {store.run_id}")


@app.command()
def stop(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = DEFAULT_RUNS_ROOT,
) -> None:
    """Ask a run to wind down cleanly: start no new tasks, then grade and report.

    This is the safe counterpart to `cancel`. Nothing in flight is killed and nothing is
    deleted, so use it whenever an experiment should end early.
    """
    store = RunStore(runs_root, run_id)
    request_stop(store.path, REASON_OPERATOR, "requested via `agent-bench stop`")
    typer.echo(f"stop requested: {store.run_id}")


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
    primary: Annotated[
        bool, typer.Option("--primary", help="Inspect the primary execution VM (the default).")
    ] = False,
    backup: Annotated[
        str | None, typer.Option("--backup", metavar="NAME", help="Inspect a named backup VM.")
    ] = None,
) -> None:
    """Show the run ID holding the execution VM lease, if any."""
    target_spec = target_profile(_selected_target(primary, backup))
    run_id = SSHBackend.active_run_id(target_spec)
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
    primary: Annotated[
        bool, typer.Option("--primary", help="Check the primary execution VM (the default).")
    ] = False,
    backup: Annotated[
        str | None, typer.Option("--backup", metavar="NAME", help="Check a named backup VM.")
    ] = None,
) -> None:
    """Check SSH connectivity and required VM executables without changing the VM."""
    target_spec = target_profile(_selected_target(primary, backup))
    SSHBackend.doctor_target(target_spec)
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
