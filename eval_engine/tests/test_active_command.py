from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_benchmark.cli import app
from agent_benchmark.config.loader import list_profiles, target_profile
from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.run.remote import SSHBackend

runner = CliRunner()


def test_active_outputs_vm_lease_owner(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BENCH_SSH_HOST", "test-vm")
    monkeypatch.setattr(SSHBackend, "active_run_id", staticmethod(lambda target: "test-run-123"))

    result = runner.invoke(app, ["active"])

    assert result.exit_code == 0, result.output
    assert result.output == "test-run-123\n"


def test_active_reports_when_vm_is_idle(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BENCH_SSH_HOST", "test-vm")
    monkeypatch.setattr(SSHBackend, "active_run_id", staticmethod(lambda target: None))

    result = runner.invoke(app, ["active"])

    assert result.exit_code == 0, result.output
    assert result.output == "no active run\n"


def test_active_run_id_reads_primary_vm_lease(monkeypatch) -> None:
    class Result:
        returncode = 0
        stdout = "test-active-run-123\n"
        stderr = ""

    calls: list[list[str]] = []
    monkeypatch.setattr(
        "agent_benchmark.run.remote.subprocess.run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )

    monkeypatch.setenv("AGENT_BENCH_SSH_HOST", "test-vm")
    assert SSHBackend.active_run_id(target_profile("primary")) == "test-active-run-123"
    from agent_benchmark.run.remote import SSH_OPTS

    assert calls == [
        [
            "ssh",
            *SSH_OPTS,
            "test-vm",
            "if [ -f /tmp/agent-benchmark/cache/leases/active/owner ]; then "
            "cat /tmp/agent-benchmark/cache/leases/active/owner; fi",
        ]
    ]


def test_active_selects_named_local_backup(monkeypatch, tmp_path: Path) -> None:
    inventory = tmp_path / "targets.local.yaml"
    inventory.write_text(
        """\
targets:
  vm-2:
    backend: ssh
    mode: backup
    host_env: AGENT_BENCH_VM_2_SSH_HOST
    remote_root: /tmp/agent-benchmark/runs
    cache_root: /tmp/agent-benchmark/cache
"""
    )
    monkeypatch.setattr("agent_benchmark.config.loader.LOCAL_TARGETS_PATH", inventory)
    monkeypatch.setenv("AGENT_BENCH_VM_2_SSH_HOST", "test-vm-2")

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "agent_benchmark.run.remote.subprocess.run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )

    result = runner.invoke(app, ["active", "--backup", "vm-2"])

    assert result.exit_code == 0, result.output
    assert result.output == "no active run\n"
    assert calls[0][0] == "ssh"
    assert "test-vm-2" in calls[0]


def test_active_rejects_primary_and_backup_together() -> None:
    result = runner.invoke(app, ["active", "--primary", "--backup", "vm-2"])

    assert result.exit_code != 0
    assert "use either --primary or --backup NAME" in result.output


def test_active_rejects_primary_profile_as_backup() -> None:
    result = runner.invoke(app, ["active", "--backup", "primary"])

    assert result.exit_code != 0
    assert "cannot be selected" in result.output
    assert "with --backup" in result.output


def test_active_rejects_removed_target_option() -> None:
    result = runner.invoke(app, ["active", "--target", "primary"])

    assert result.exit_code != 0
    assert "No such option: --target" in result.output


def test_profile_listing_includes_local_backups(monkeypatch, tmp_path: Path) -> None:
    inventory = tmp_path / "targets.local.yaml"
    inventory.write_text(
        """\
targets:
  vm-3:
    backend: ssh
    mode: backup
    host_env: AGENT_BENCH_VM_3_SSH_HOST
    remote_root: /tmp/agent-benchmark/runs
    cache_root: /tmp/agent-benchmark/cache
"""
    )
    monkeypatch.setattr("agent_benchmark.config.loader.LOCAL_TARGETS_PATH", inventory)

    assert list_profiles()["targets"] == ["primary", "vm-3"]


def test_local_inventory_cannot_replace_primary(monkeypatch, tmp_path: Path) -> None:
    inventory = tmp_path / "targets.local.yaml"
    inventory.write_text(
        """\
targets:
  primary:
    backend: ssh
    mode: backup
    host_env: WRONG_HOST
    remote_root: /tmp/agent-benchmark/runs
    cache_root: /tmp/agent-benchmark/cache
"""
    )
    monkeypatch.setattr("agent_benchmark.config.loader.LOCAL_TARGETS_PATH", inventory)

    with pytest.raises(ConfigurationError, match="cannot replace built-in target"):
        target_profile("primary")
