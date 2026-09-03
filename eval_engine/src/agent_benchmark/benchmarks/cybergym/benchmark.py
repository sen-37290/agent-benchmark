from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.benchmarks.cybergym.pool import catalog, create_pool
from agent_benchmark.benchmarks.cybergym.results import normalize
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.harnesses.cybergym_grades import fold_grades
from agent_benchmark.run.process import run_logged
from agent_benchmark.run.result import TaskResult
from agent_benchmark.run.stop import stop_reason

CYBERGYM_REVISION = "7656b71d07da6694e262f9c34ea994cd4849c0eb"
AGENT_EXAMPLES_REVISION = "6660f3f5a0193e7f142f23f998677429edb3d18c"
DATASET_REVISION = "bde190ded494e52bc684b66073b436c9d992c7c6"
BINARY_DATA_REVISION = "11ab39bd443379e3f710049eca2f5be3a3eae793"

# Poetry configuration for the isolated OpenHands 3.12 build.  Forcing an in-project
# virtualenv keeps OpenHands' dependencies out of the engine's 3.14 environment.
OPENHANDS_BUILD_ENV = {
    "POETRY_VIRTUALENVS_CREATE": "true",
    "POETRY_VIRTUALENVS_IN_PROJECT": "true",
    "POETRY_NO_INTERACTION": "1",
}


def _isolated_poetry(command: list[str]) -> list[str]:
    """Run a Poetry-driven command with the engine's active virtualenv stripped.

    ``run_logged`` merges over ``os.environ`` and therefore cannot unset variables, so
    the engine's ``VIRTUAL_ENV``/``POETRY_ACTIVE`` are cleared with an ``env -u`` prefix.
    """
    return ["env", "-u", "VIRTUAL_ENV", "-u", "POETRY_ACTIVE", *command]


def _ids(run_dir: Path, spec: ResolvedSpec) -> list[str]:
    data = json.loads((run_dir / spec.benchmark.pool_path).read_text())
    return list(data["instance_ids"])


class CyberGym(BenchmarkPlugin):
    name = "cybergym"
    compatible_harnesses = frozenset({"cybergym-native"})

    def __init__(self, kind: str):
        self.kind = kind

    def create_pool(self, output_path: Path, sampling: str | None, size: int | None) -> None:
        create_pool(self.kind, output_path, sampling, size)

    def prepare(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        settings = spec.benchmark.settings
        if spec.benchmark.harness != "cybergym-native":
            raise ConfigurationError("CyberGym only supports the native official harness")
        if spec.model.subject_agent != "openhands":
            raise ConfigurationError("CyberGym only supports the official OpenHands scaffold")
        for key, expected in {
            "cybergym_revision": CYBERGYM_REVISION,
            "agent_examples_revision": AGENT_EXAMPLES_REVISION,
            "dataset_revision": DATASET_REVISION,
            "binary_dataset_revision": BINARY_DATA_REVISION,
        }.items():
            if str(settings.get(key)) != expected:
                raise StageError(
                    f"CyberGym {key} is not pinned to the official revision {expected}"
                )
        ids = _ids(run_dir, spec)
        expected_catalog = catalog(self.kind)
        known = {task["id"] for task in expected_catalog["tasks"]}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise StageError(f"CyberGym pool contains unknown tasks: {', '.join(unknown)}")
        # A pinned pool (single-instance or explicit subset re-run) is an intentional partial
        # selection, so the profile's fixed size is not enforced; all ids are still validated
        # against the catalog above. Sampled/full pools remain strictly size-gated.
        pool_payload = json.loads((run_dir / spec.benchmark.pool_path).read_text())
        pinned = pool_payload.get("sampling") in {"pinned", "pinned-subset"}
        allowed_sizes = {len(expected_catalog["tasks"])}
        if self.kind == "cybergym_smoke":
            allowed_sizes.add(1)
        if not pinned and len(ids) not in allowed_sizes:
            raise StageError("CyberGym pool size does not match its profile")

        gates = cache_root / "cybergym" / "gates"
        if settings.get("require_canary_gate") and not (gates / "canary.json").is_file():
            raise StageError("CyberGym 300-task runs require a completed one-task canary")
        if settings.get("require_smoke_gate") and not (gates / "smoke.json").is_file():
            raise StageError("CyberGym 300-task runs require a completed 10-task smoke run")

        root = cache_root / "cybergym"
        root.mkdir(parents=True, exist_ok=True)
        log = run_dir / "logs" / "prepare.log"
        # Ensure the OpenHands sandbox image is cached locally before anything else.  This
        # runs outside the provenance short-circuit because the agent container starts on
        # an internal, network-less Docker network and can never pull it itself.
        self._prepare_runtime_image(str(settings["runtime_image"]), log)
        # The CyberGym server runs every submitted PoC inside this runner image; if it is
        # absent, /submit-vul fails with ImageNotFound -> HTTP 500 and nothing can be graded.
        self._prepare_runner_image(log)
        provenance = root / "provenance.json"
        expected = {
            "cybergym_revision": CYBERGYM_REVISION,
            "agent_examples_revision": AGENT_EXAMPLES_REVISION,
            "dataset_revision": DATASET_REVISION,
            "binary_data_revision": BINARY_DATA_REVISION,
            "pool_sha256": spec.benchmark.pool_sha256,
            "task_count": len(ids),
        }
        if provenance.is_file() and json.loads(provenance.read_text()) == expected:
            return

        source = root / "cybergym-source"
        examples = root / "agent-examples"
        source.mkdir(parents=True, exist_ok=True)
        examples.mkdir(parents=True, exist_ok=True)
        # The actual large-data acquisition is intentionally explicit and pinned.  These
        # commands are idempotent and are run on the target VM, never inside agent containers.
        for directory, url, revision in (
            (source, "https://github.com/sunblaze-ucb/cybergym.git", CYBERGYM_REVISION),
            (
                examples,
                "https://github.com/sunblaze-ucb/cybergym-agent-examples.git",
                AGENT_EXAMPLES_REVISION,
            ),
        ):
            if not (directory / ".git").is_dir():
                run_logged(["git", "init"], cwd=directory, log_path=log)
                run_logged(["git", "remote", "add", "origin", url], cwd=directory, log_path=log)
            run_logged(
                ["git", "fetch", "--depth", "1", "origin", revision], cwd=directory, log_path=log
            )
            run_logged(["git", "checkout", "--detach", revision], cwd=directory, log_path=log)

        data_root = root / "data"
        data_root.mkdir(exist_ok=True)
        self._fetch_selected_data(data_root, ids, log)
        self._prepare_binary_data(root, log)
        self._prepare_openhands(examples / "openhands", root, log)
        provenance.write_text(json.dumps(expected, indent=2) + "\n")

    @staticmethod
    def _fetch_selected_data(data_root: Path, ids: list[str], log: Path) -> None:
        marker = data_root / ".selected-assets.json"
        expected = {"task_ids": sorted(ids), "level": "level1", "revision": DATASET_REVISION}
        if marker.is_file() and json.loads(marker.read_text()) == expected:
            return
        # Fetch immutable files directly from the pinned Hugging Face revision.  The
        # dataset repository uses Git LFS/promisor objects; direct resolves avoid
        # intermittent partial-clone failures while retaining exact revision pins.
        base = f"https://huggingface.co/datasets/sunblaze-ucb/cybergym/resolve/{DATASET_REVISION}"
        run_logged(
            [
                "wget",
                "-q",
                "--show-progress",
                "-O",
                str(data_root / "tasks.json"),
                f"{base}/tasks.json",
            ],
            cwd=data_root,
            log_path=log,
        )
        for task_id in ids:
            family, ident = task_id.split(":", 1)
            destination = data_root / family / ident
            destination.mkdir(parents=True, exist_ok=True)
            for filename in ("repo-vul.tar.gz", "description.txt"):
                target = destination / filename
                # The pinned revision is immutable, so a non-empty file already on disk is
                # correct; skipping it lets a subset re-run reuse the full catalog's cache
                # without re-downloading any per-task asset.
                if target.is_file() and target.stat().st_size > 0:
                    continue
                run_logged(
                    [
                        "wget",
                        "-q",
                        "--show-progress",
                        "-O",
                        str(target),
                        f"{base}/data/{family}/{ident}/{filename}",
                    ],
                    cwd=data_root,
                    log_path=log,
                )
        marker.write_text(json.dumps(expected, indent=2) + "\n")

    @staticmethod
    def _prepare_runtime_image(runtime_image: str, log: Path) -> None:
        # The pinned OpenHands runtime registry host (docker.all-hands.dev) no longer
        # resolves in public DNS.  The byte-identical image is published on ghcr.io, so
        # pull it from there and retag it to the pinned name.  The agent's sandbox then
        # finds the image locally and never attempts a pull over its network-less runtime.
        present = subprocess.run(
            ["docker", "image", "inspect", runtime_image],
            capture_output=True,
            text=True,
        )
        if present.returncode == 0:
            return
        _, _, repo_tag = runtime_image.partition("/")
        if not repo_tag:
            raise StageError(f"unexpected runtime image reference: {runtime_image!r}")
        mirror = f"ghcr.io/{repo_tag}"
        run_logged(["docker", "pull", mirror], cwd=Path.cwd(), log_path=log)
        run_logged(["docker", "tag", mirror, runtime_image], cwd=Path.cwd(), log_path=log)

    # DEFAULT_RUNNER_IMAGE in cybergym.server.server_utils; published on Docker Hub. Every
    # PoC (arvo tasks and any oss-fuzz task without a per-task runner) runs inside it.
    RUNNER_IMAGE = "cybergym/oss-fuzz-base-runner:latest"

    @classmethod
    def _prepare_runner_image(cls, log: Path) -> None:
        present = subprocess.run(
            ["docker", "image", "inspect", cls.RUNNER_IMAGE],
            capture_output=True,
            text=True,
        )
        if present.returncode == 0:
            return
        run_logged(["docker", "pull", cls.RUNNER_IMAGE], cwd=Path.cwd(), log_path=log)

    @staticmethod
    def _prepare_binary_data(root: Path, log: Path) -> None:
        binary = root / "cybergym-server-data"
        marker = binary / ".ready"
        if marker.is_file():
            return
        archive = root / "cybergym-server-data.7z"
        if not archive.exists():
            run_logged(
                [
                    "wget",
                    "-O",
                    str(archive),
                    "https://huggingface.co/datasets/sunblaze-ucb/cybergym-server-binary/resolve/11ab39bd443379e3f710049eca2f5be3a3eae793/cybergym-server-data.7z",
                ],
                cwd=root,
                log_path=log,
            )
        binary.mkdir(parents=True, exist_ok=True)
        run_logged(["7z", "x", "-y", str(archive), f"-o{binary}"], cwd=root, log_path=log)
        marker.write_text("ready\n")

    @staticmethod
    def _prepare_openhands(agent_dir: Path, root: Path, log: Path) -> None:
        marker = agent_dir / ".agent-bench-ready"
        if marker.is_file():
            return
        repo = agent_dir / "openhands-repo"
        # The marker is only written once `make build` succeeds, so reaching here means a
        # previous attempt either never ran or failed partway through.  A failed build can
        # leave a partially-populated (and possibly 3.14-contaminated) in-project venv
        # behind; remove it so every rebuild starts from a clean 3.12 environment.
        stale_venv = repo / ".venv"
        if stale_venv.exists():
            shutil.rmtree(stale_venv)
        run_logged(
            ["uv", "python", "install", "3.12"],
            cwd=repo,
            log_path=log,
        )
        run_logged(
            [
                "sh",
                "-c",
                "python_bin=$(uv python find 3.12) && "
                'sudo ln -sf "$python_bin" /usr/local/bin/python3.12',
            ],
            cwd=repo,
            log_path=log,
        )
        # Every Poetry-driven step (including the ones the Makefile nests inside
        # `make build`) must run with the engine's Python 3.14 virtualenv stripped from
        # the environment.  Poetry 1.8 gives an activated VIRTUAL_ENV precedence over the
        # interpreter chosen by `poetry env use`, so leaving it set makes the nested
        # `poetry install` build native wheels (torch, greenlet, ...) against 3.14.  We
        # also pin an in-project 3.12 virtualenv so Poetry cannot reuse the engine's.
        run_logged(
            _isolated_poetry(["poetry", "env", "use", "/usr/local/bin/python3.12"]),
            cwd=repo,
            log_path=log,
            env=OPENHANDS_BUILD_ENV,
        )
        run_logged(
            _isolated_poetry(["poetry", "install", "--no-interaction"]),
            cwd=repo,
            log_path=log,
            env=OPENHANDS_BUILD_ENV,
        )
        run_logged(
            _isolated_poetry(["make", "build", "INSTALL_PLAYWRIGHT=false"]),
            cwd=repo,
            log_path=log,
            env=OPENHANDS_BUILD_ENV,
        )
        marker.write_text("ready\n")

    def grade(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        out = run_dir / "artifacts" / "cybergym"
        # Rebuild grades.json from the append-only ledger when execute did not finish. For a clean
        # run this is a no-op: grades.json already holds every task and wins over the ledger.
        grades = fold_grades(out)
        if not grades:
            raise StageError("CyberGym native harness did not produce grades.json")
        ids = _ids(run_dir, spec)
        missing = set(ids) - set(grades)
        if set(grades) - set(ids):
            raise StageError("CyberGym grades contain tasks that are not in the pool")
        if missing:
            reason = stop_reason(run_dir)
            if reason is None:
                raise StageError("CyberGym grades do not match the pool")
            # The run was deliberately stopped (cost cap, operator stop). Grade what finished and
            # let normalize record the remainder as unrun rather than discarding the whole run.
            _log_partial(run_dir, reason, graded=len(grades), total=len(ids))
        if spec.benchmark.sample_size == 1:
            self._write_gate(cache_root, "canary", spec, grades)
        elif spec.benchmark.sample_size == 10:
            self._write_gate(cache_root, "smoke", spec, grades)

    @staticmethod
    def _write_gate(cache_root: Path, name: str, spec: ResolvedSpec, grades: dict) -> None:
        if any(item.get("infrastructure_error") for item in grades.values()):
            return
        if len(grades) != spec.benchmark.sample_size:
            return  # a partial run must never certify a canary/smoke gate
        gate = cache_root / "cybergym" / "gates" / f"{name}.json"
        gate.parent.mkdir(parents=True, exist_ok=True)
        gate.write_text(
            json.dumps(
                {
                    "run_id": spec.run_id,
                    "pool_sha256": spec.benchmark.pool_sha256,
                    "grades": len(grades),
                },
                indent=2,
            )
            + "\n"
        )

    def normalize(self, spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
        return normalize(spec, run_dir)


def _log_partial(run_dir: Path, reason: str, *, graded: int, total: int) -> None:
    """Record that grading ran over an intentionally incomplete pool."""
    note = f"grading a partial pool: {graded}/{total} tasks completed (stop reason: {reason})\n"
    log = run_dir / "logs" / "grade.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(note)
    print(note, end="", flush=True)
