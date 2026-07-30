from typer.testing import CliRunner

from agent_benchmark.cli import app
from agent_benchmark.run.remote import active_run_id

runner = CliRunner()


def test_active_outputs_vm_lease_owner(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BENCH_SSH_HOST", "test-vm")
    monkeypatch.setattr("agent_benchmark.cli.active_run_id", lambda host, root: "test-run-123")

    result = runner.invoke(app, ["active"])

    assert result.exit_code == 0, result.output
    assert result.output == "test-run-123\n"


def test_active_reports_when_vm_is_idle(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BENCH_SSH_HOST", "test-vm")
    monkeypatch.setattr("agent_benchmark.cli.active_run_id", lambda host, root: None)

    result = runner.invoke(app, ["active"])

    assert result.exit_code == 0, result.output
    assert result.output == "no active run\n"


def test_active_run_id_reads_vm_lease(monkeypatch) -> None:
    class Result:
        returncode = 0
        stdout = "test-active-run-123\n"
        stderr = ""

    calls: list[list[str]] = []
    monkeypatch.setattr(
        "agent_benchmark.run.remote.subprocess.run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )

    assert active_run_id("test-vm", "/tmp/agent-benchmark/cache") == "test-active-run-123"
    assert calls == [
        [
            "ssh",
            "test-vm",
            "if [ -f /tmp/agent-benchmark/cache/leases/active/owner ]; then "
            "cat /tmp/agent-benchmark/cache/leases/active/owner; fi",
        ]
    ]
