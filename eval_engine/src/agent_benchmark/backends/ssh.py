from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from agent_benchmark.errors import ConfigurationError, IntegrityError, StageError
from agent_benchmark.models import ResolvedSpec

SAFE_RUN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{5,127}$")


class SSHBackend:
    """Stateless SSH/rsync transport for a long-lived execution VM."""

    def __init__(self, spec: ResolvedSpec, project_root: Path):
        self.spec = spec
        self.project_root = project_root.resolve()
        self.host = os.environ.get(spec.target.host_env, "").strip()
        if not self.host:
            raise ConfigurationError(
                f"set {spec.target.host_env} to the fixed VM SSH host or alias"
            )
        if not SAFE_RUN_ID.fullmatch(spec.run_id):
            raise ConfigurationError(f"unsafe run id: {spec.run_id!r}")
        self.remote_run = PurePosixPath(spec.target.remote_root) / spec.run_id
        self.remote_source = self.remote_run / "source"
        self.remote_cache = PurePosixPath(spec.target.cache_root)
        self.remote_lease = self.remote_cache / "leases" / "active"

    def _ssh(
        self,
        command: Sequence[str] | str,
        *,
        input_text: str | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        remote = command if isinstance(command, str) else " ".join(shlex.quote(x) for x in command)
        result = subprocess.run(
            ["ssh", self.host, remote],
            input=input_text,
            text=True,
            capture_output=capture,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() if capture else "see SSH output above"
            raise StageError(f"remote command failed ({result.returncode}): {detail}")
        return result

    def doctor(self) -> None:
        checks = (
            "command -v python3 && command -v uv && command -v docker && command -v rsync "
            "&& docker compose version >/dev/null"
        )
        self._ssh(["sh", "-c", checks])

    def deploy(self, local_run: Path) -> None:
        # Atomic directory creation is the VM-wide single-run lease. A stale lease is deliberately
        # not stolen automatically; the operator must resume its run or inspect the VM first.
        lease_script = f"""
set -eu
mkdir -p {shlex.quote(str(self.remote_cache / "leases"))}
if mkdir {shlex.quote(str(self.remote_lease))} 2>/dev/null; then
  printf '%s\\n' {shlex.quote(self.spec.run_id)} > {shlex.quote(str(self.remote_lease / "owner"))}
else
  owner=$(cat {shlex.quote(str(self.remote_lease / "owner"))} 2>/dev/null || true)
  if [ "$owner" != {shlex.quote(self.spec.run_id)} ]; then
    echo "VM is leased by run: $owner" >&2
    exit 73
  fi
fi
mkdir -p {shlex.quote(str(self.remote_source))}
mkdir -p {shlex.quote(str(self.remote_run / "artifacts"))}
mkdir -p {shlex.quote(str(self.remote_run / "logs"))}
"""
        self._ssh(lease_script)
        excludes = [
            "--exclude=.venv",
            "--exclude=.git",
            "--exclude=.env",
            "--exclude=runs",
            "--exclude=pools",
            "--exclude=tests",
            "--exclude=dist",
            "--exclude=.pytest_cache",
            "--exclude=.ruff_cache",
            "--exclude=__pycache__",
        ]
        result = subprocess.run(
            [
                "rsync",
                "-az",
                "--delete",
                *excludes,
                f"{self.project_root}/",
                f"{self.host}:{self.remote_source}/",
            ],
            text=True,
            check=False,
        )
        if result.returncode:
            raise StageError(f"source deployment failed with exit code {result.returncode}")
        self._rsync_file(local_run / "resolved.yaml", self.remote_run / "resolved.yaml")
        result = subprocess.run(
            [
                "rsync",
                "-az",
                f"{local_run / 'inputs'}/",
                f"{self.host}:{self.remote_run}/inputs/",
            ],
            text=True,
            check=False,
        )
        if result.returncode:
            raise StageError("failed to transfer run inputs")

        key_name = self.spec.model.api_key_env
        key = os.environ.get(key_name, "")
        if not key:
            raise ConfigurationError(f"set {key_name} before running the experiment")
        secrets = json.dumps({key_name: key})
        self._ssh(
            f"umask 077; cat > {shlex.quote(str(self.remote_run / 'secrets.json'))}",
            input_text=secrets,
        )
        self._ssh(f"cd {shlex.quote(str(self.remote_source))} && uv sync --frozen --extra swebench")

    def run_worker(self, stage: str) -> None:
        command = (
            f"cd {shlex.quote(str(self.remote_source))} && "
            f"uv run agent-bench worker {shlex.quote(stage)} "
            f"--spec {shlex.quote(str(self.remote_run / 'resolved.yaml'))} "
            f"--run-dir {shlex.quote(str(self.remote_run))} "
            f"--cache-root {shlex.quote(str(self.remote_cache))}"
        )
        self._ssh(command)

    def collect(self, local_run: Path) -> None:
        self._ssh(
            f"cd {shlex.quote(str(self.remote_source))} && "
            f"uv run agent-bench worker manifest --run-dir {shlex.quote(str(self.remote_run))}"
        )
        for directory in ("artifacts", "logs"):
            target = local_run / directory
            target.mkdir(parents=True, exist_ok=True)
            source = f"{self.host}:{self.remote_run}/{directory}/"
            result = subprocess.run(
                ["rsync", "-az", "--delete", source, f"{target}/"],
                text=True,
                check=False,
            )
            if result.returncode:
                raise StageError(f"artifact transfer failed for {directory}")
        manifest_source = f"{self.host}:{self.remote_run}/artifact-manifest.json"
        result = subprocess.run(
            ["rsync", "-az", manifest_source, f"{local_run}/"], text=True, check=False
        )
        if result.returncode:
            raise StageError("artifact manifest transfer failed")
        self.verify(local_run)

    def verify(self, local_run: Path) -> None:
        manifest_path = local_run / "artifact-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for relative, expected in manifest["files"].items():
            path = local_run / relative
            if not path.is_file():
                raise IntegrityError(f"collected artifact is missing: {relative}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected["sha256"] or path.stat().st_size != expected["size"]:
                raise IntegrityError(f"collected artifact failed verification: {relative}")

    def cleanup(self) -> None:
        # Use Python and resolved absolute paths rather than a recursive shell command. The guard
        # refuses broad roots even if a target profile is misconfigured.
        script = (
            "import pathlib,shutil,sys; "
            f"root=pathlib.Path({str(PurePosixPath(self.spec.target.remote_root))!r}).resolve(); "
            f"target=pathlib.Path({str(self.remote_run)!r}).resolve(); "
            "assert root != pathlib.Path('/') and len(root.parts) >= 3; "
            "assert target.parent == root; "
            "shutil.rmtree(target,ignore_errors=True); "
        )
        self._ssh(["python3", "-c", script])
        release_script = (
            f"import pathlib,shutil; shutil.rmtree(pathlib.Path({str(self.remote_lease)!r}))"
        )
        release = (
            f"owner=$(cat {shlex.quote(str(self.remote_lease / 'owner'))} 2>/dev/null || true); "
            f'if [ "$owner" = {shlex.quote(self.spec.run_id)} ]; then '
            f"python3 -c {shlex.quote(release_script)}; fi"
        )
        self._ssh(release)

    def _rsync_file(self, source: Path, destination: PurePosixPath) -> None:
        result = subprocess.run(
            ["rsync", "-az", str(source), f"{self.host}:{destination}"],
            text=True,
            check=False,
        )
        if result.returncode:
            raise StageError(f"failed to transfer {source.name}")
