from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from agent_benchmark.config.schema import ResolvedSpec, TargetSpec
from agent_benchmark.exceptions import ConfigurationError, IntegrityError, StageError

SAFE_RUN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{5,127}$")
# Keepalive so a stalled TCP connection fails within ~1 minute instead of hanging the
# pipeline indefinitely (a silent rsync/ssh stall once wedged a run for hours).
SSH_OPTS = (
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=4",
    "-o",
    "ConnectTimeout=30",
)
RSYNC_RSH = "ssh " + " ".join(SSH_OPTS)
SOURCE_RSYNC_EXCLUDES = (
    "/.venv/",
    "/.git/",
    "/.env",
    "/.agent-bench/",
    "/runs/",
    "/pools/",
    "/tests/",
    "/dist/",
    "/.pytest_cache/",
    "/.ruff_cache/",
    "__pycache__/",
)


class SSHBackend:
    """Stateless SSH/rsync transport for one selected execution VM."""

    def __init__(self, spec: ResolvedSpec, project_root: Path):
        self.spec = spec
        self.project_root = project_root.resolve()
        self.target_name = spec.target.profile
        self.mode = spec.target.mode
        self.host = self.target_host(spec.target)
        if not SAFE_RUN_ID.fullmatch(spec.run_id):
            raise ConfigurationError(f"unsafe run id: {spec.run_id!r}")
        self.remote_run = PurePosixPath(spec.target.remote_root) / spec.run_id
        self.remote_source = self.remote_run / "source"
        self.remote_cache = PurePosixPath(spec.target.cache_root)
        self.remote_lease = self.remote_cache / "leases" / "active"

    @staticmethod
    def target_host(target: TargetSpec) -> str:
        host = os.environ.get(target.host_env, "").strip()
        if not host:
            raise ConfigurationError(
                f"set {target.host_env} to the SSH host or alias for target {target.profile!r}"
            )
        return host

    @staticmethod
    def _ssh_host(
        host: str,
        command: Sequence[str] | str,
        *,
        input_text: str | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        remote = command if isinstance(command, str) else " ".join(shlex.quote(x) for x in command)
        result = subprocess.run(
            ["ssh", *SSH_OPTS, host, remote],
            input=input_text,
            text=True,
            capture_output=capture,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() if capture else "see SSH output above"
            raise StageError(f"remote command failed ({result.returncode}): {detail}")
        return result

    @classmethod
    def active_run_id(cls, target: TargetSpec) -> str | None:
        """Return the run holding the selected VM's lease, if any."""
        owner = PurePosixPath(target.cache_root) / "leases" / "active" / "owner"
        command = f"if [ -f {shlex.quote(str(owner))} ]; then cat {shlex.quote(str(owner))}; fi"
        result = cls._ssh_host(cls.target_host(target), command, capture=True)
        run_id = result.stdout.strip()
        if not run_id:
            return None
        if not SAFE_RUN_ID.fullmatch(run_id):
            raise StageError(f"VM lease contains an invalid run ID: {run_id!r}")
        return run_id

    @classmethod
    def doctor_target(cls, target: TargetSpec) -> None:
        checks = (
            "command -v python3 && command -v uv && command -v docker && command -v rsync "
            "&& docker compose version >/dev/null"
        )
        cls._ssh_host(cls.target_host(target), ["sh", "-c", checks])

    def _ssh(
        self,
        command: Sequence[str] | str,
        *,
        input_text: str | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return self._ssh_host(
            self.host,
            command,
            input_text=input_text,
            capture=capture,
        )

    def doctor(self) -> None:
        self.doctor_target(self.spec.target)

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
        # Leading slashes anchor project-level exclusions at the transfer root. In particular,
        # an unanchored ``tests`` pattern also removes the benchmark asset at
        # ``src/agent_benchmark/benchmarks/swebench_verified/assets/tests``.
        excludes = [f"--exclude={pattern}" for pattern in SOURCE_RSYNC_EXCLUDES]
        result = subprocess.run(
            [
                "rsync",
                "-az",
                "-e",
                RSYNC_RSH,
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
                "-e",
                RSYNC_RSH,
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
        dependency_extra = self.spec.benchmark.settings.get("dependency_extra")
        sync = "uv sync --frozen"
        if dependency_extra is not None:
            dependency_extra = str(dependency_extra)
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", dependency_extra):
                raise ConfigurationError(f"unsafe dependency extra: {dependency_extra!r}")
            sync += f" --extra {shlex.quote(dependency_extra)}"
        self._ssh(f"cd {shlex.quote(str(self.remote_source))} && {sync}")

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
                ["rsync", "-az", "-e", RSYNC_RSH, "--delete", source, f"{target}/"],
                text=True,
                check=False,
            )
            if result.returncode:
                raise StageError(f"artifact transfer failed for {directory}")
        manifest_source = f"{self.host}:{self.remote_run}/artifact-manifest.json"
        result = subprocess.run(
            ["rsync", "-az", "-e", RSYNC_RSH, manifest_source, f"{local_run}/"],
            text=True,
            check=False,
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

    def cancel(self) -> None:
        """Stop and remove resources belonging to this exact run, then release its lease."""
        script = f"""
import json
import os
import pathlib
import shutil
import signal
import subprocess
import time

run_id = {self.spec.run_id!r}
root = pathlib.Path({str(PurePosixPath(self.spec.target.remote_root))!r}).resolve()
target = pathlib.Path({str(self.remote_run)!r}).resolve()
lease = pathlib.Path({str(self.remote_lease)!r}).resolve()
assert root != pathlib.Path("/") and len(root.parts) >= 3
assert target.parent == root and target.name == run_id

needle = str(target).encode()
excluded = {{os.getpid(), os.getppid()}}

def matching_pids():
    matches = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in excluded:
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if needle in command:
            matches.append(pid)
    return matches

for pid in matching_pids():
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

deadline = time.monotonic() + 10
while time.monotonic() < deadline and matching_pids():
    time.sleep(0.2)
for pid in matching_pids():
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

container_ids = subprocess.run(
    ["docker", "ps", "-aq"], check=True, capture_output=True, text=True
).stdout.split()
projects = set()
owned_containers = []
if container_ids:
    inspected = json.loads(
        subprocess.run(
            ["docker", "inspect", *container_ids], check=True, capture_output=True, text=True
        ).stdout
    )
    for item in inspected:
        labels = item.get("Config", {{}}).get("Labels") or {{}}
        mounts = item.get("Mounts") or []
        values = [str(value) for value in labels.values()]
        values.extend(str(mount.get("Source", "")) for mount in mounts)
        if any(str(target) in value for value in values):
            owned_containers.append(item["Id"])
            project = labels.get("com.docker.compose.project")
            if project:
                projects.add(project)
if owned_containers:
    subprocess.run(["docker", "rm", "-f", *owned_containers], check=False)

for resource, remove_command in (
    ("network", ["docker", "network", "rm"]),
    ("volume", ["docker", "volume", "rm", "-f"]),
):
    ids = subprocess.run(
        ["docker", resource, "ls", "-q"], check=True, capture_output=True, text=True
    ).stdout.split()
    for resource_id in ids:
        details = json.loads(
            subprocess.run(
                ["docker", resource, "inspect", resource_id],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )[0]
        labels = details.get("Labels") or {{}}
        if labels.get("com.docker.compose.project") in projects:
            subprocess.run([*remove_command, resource_id], check=False)

shutil.rmtree(target, ignore_errors=True)
owner_path = lease / "owner"
try:
    owner = owner_path.read_text().strip()
except OSError:
    owner = ""
if owner == run_id:
    shutil.rmtree(lease, ignore_errors=True)

print(
    f"cancelled {{run_id}}: processes stopped, "
    f"containers={{len(owned_containers)}}, compose_projects={{len(projects)}}"
)
"""
        self._ssh("python3 -", input_text=script)

    def _rsync_file(self, source: Path, destination: PurePosixPath) -> None:
        result = subprocess.run(
            ["rsync", "-az", "-e", RSYNC_RSH, str(source), f"{self.host}:{destination}"],
            text=True,
            check=False,
        )
        if result.returncode:
            raise StageError(f"failed to transfer {source.name}")
