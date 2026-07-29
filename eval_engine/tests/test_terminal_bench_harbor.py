import json
from pathlib import Path

from typer.testing import CliRunner

from agent_benchmark.benchmarks.terminal_bench.pool import create_pool
from agent_benchmark.cli import app
from agent_benchmark.config.loader import resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.harnesses.harbor import build_command
from agent_benchmark.run.remote import SSHBackend

ROOT = Path(__file__).parents[1]


def terminal_spec(tmp_path: Path):
    pool = tmp_path / "pool.json"
    create_pool(pool, "random", 2)
    request = UserRequest(
        benchmark="terminal-bench-2.1",
        sampling="random",
        size=2,
        model="glm-5.2",
        reasoning_effort="xhigh",
        provider="openrouter",
        workers=2,
        budget_usd=10,
    )
    spec = resolve(request, "test-terminal-run", ROOT, pool)
    run_dir = tmp_path / "run"
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / spec.benchmark.pool_path).write_text(pool.read_text())
    return spec, run_dir


def test_builds_native_package_dataset_command(tmp_path: Path) -> None:
    spec, run_dir = terminal_spec(tmp_path)
    command = build_command(spec, run_dir, tmp_path / "cache", "secret")
    pool = json.loads((run_dir / spec.benchmark.pool_path).read_text())

    assert command[:4] == ["uv", "run", "harbor", "run"]
    assert command[command.index("-d") + 1] == spec.benchmark.dataset_id
    assert command.count("--include-task-name") == 2
    assert all(task_id in command for task_id in pool["instance_ids"])
    assert command[command.index("-k") + 1] == "1"
    assert command[command.index("--job-name") + 1] == spec.run_id


def test_resumes_existing_native_job(tmp_path: Path) -> None:
    spec, run_dir = terminal_spec(tmp_path)
    job_dir = run_dir / "artifacts" / "harbor_jobs" / spec.run_id
    job_dir.mkdir(parents=True)
    (job_dir / "config.json").write_text("{}")

    assert build_command(spec, run_dir, tmp_path / "cache", "secret") == [
        "uv",
        "run",
        "harbor",
        "job",
        "resume",
        "-p",
        str(job_dir),
    ]


def test_swebench_keeps_local_dataset_command(tmp_path: Path) -> None:
    pool_file = tmp_path / "swe-pool.json"
    pool_file.write_text(json.dumps({"instance_ids": ["django__django-13741"]}))
    request = UserRequest(
        benchmark="swebench-verified",
        model="glm-5.2",
        reasoning_effort="xhigh",
        provider="openrouter",
        workers=1,
        budget_usd=10,
    )
    spec = resolve(request, "test-swe-local", ROOT, pool_file)
    run_dir = tmp_path / "run"
    cache_root = tmp_path / "cache"
    command = build_command(spec, run_dir, cache_root, "secret")

    assert "-p" in command
    assert "-d" not in command
    assert "--include-task-name" not in command
    assert spec.run_id in command


def test_cli_plan_resolves_terminal_bench_without_vm_or_model() -> None:
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "--benchmark",
            "terminal-bench-2.1",
            "--model",
            "glm-5.2",
            "--reasoning-effort",
            "xhigh",
            "--provider",
            "friendli",
            "--workers",
            "1",
            "--budget-usd",
            "5",
            "--sampling",
            "category",
            "--size",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "plugin: terminal_bench" in result.output
    assert "grading: harbor-inline-verifier" in result.output
    assert "sample_size: 2" in result.output


def test_remote_deploy_selects_terminalbench_extra(tmp_path: Path, monkeypatch) -> None:
    spec, run_dir = terminal_spec(tmp_path)
    (run_dir / "resolved.yaml").write_text("spec: test\n")
    monkeypatch.setenv("AGENT_BENCH_SSH_HOST", "unused-test-host")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    backend = SSHBackend(spec, ROOT)
    commands: list[str] = []
    monkeypatch.setattr(backend, "_ssh", lambda command, **_: commands.append(str(command)))
    monkeypatch.setattr(backend, "_rsync_file", lambda *args: None)
    monkeypatch.setattr(
        "agent_benchmark.run.remote.subprocess.run",
        lambda *args, **kwargs: type("Completed", (), {"returncode": 0})(),
    )

    backend.deploy(run_dir)

    assert any("uv sync --frozen --extra terminalbench" in command for command in commands)
