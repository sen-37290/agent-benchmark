from __future__ import annotations

import json
from pathlib import Path

from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.benchmarks.cybergym.pool import catalog, create_pool
from agent_benchmark.benchmarks.cybergym.results import normalize
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.run.process import run_logged
from agent_benchmark.run.result import TaskResult

CYBERGYM_REVISION = "7656b71d07da6694e262f9c34ea994cd4849c0eb"
AGENT_EXAMPLES_REVISION = "6660f3f5a0193e7f142f23f998677429edb3d18c"
DATASET_REVISION = "bde190ded494e52bc684b66073b436c9d992c7c6"
BINARY_DATA_REVISION = "11ab39bd443379e3f710049eca2f5be3a3eae793"


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
        allowed_sizes = {len(expected_catalog["tasks"])}
        if self.kind == "cybergym_smoke":
            allowed_sizes.add(1)
        if len(ids) not in allowed_sizes:
            raise StageError("CyberGym pool size does not match its profile")

        gates = cache_root / "cybergym" / "gates"
        if settings.get("require_canary_gate") and not (gates / "canary.json").is_file():
            raise StageError("CyberGym 300-task runs require a completed one-task canary")
        if settings.get("require_smoke_gate") and not (gates / "smoke.json").is_file():
            raise StageError("CyberGym 300-task runs require a completed 10-task smoke run")

        root = cache_root / "cybergym"
        root.mkdir(parents=True, exist_ok=True)
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
        log = run_dir / "logs" / "prepare.log"
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
                run_logged(
                    [
                        "wget",
                        "-q",
                        "--show-progress",
                        "-O",
                        str(destination / filename),
                        f"{base}/data/{family}/{ident}/{filename}",
                    ],
                    cwd=data_root,
                    log_path=log,
                )
        marker.write_text(json.dumps(expected, indent=2) + "\n")

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
        run_logged(
            [
                "uv",
                "run",
                "--python",
                "3.12",
                "--with",
                "poetry",
                "poetry",
                "install",
                "--no-interaction",
            ],
            cwd=agent_dir / "openhands-repo",
            log_path=log,
        )
        run_logged(
            ["make", "build", "INSTALL_PLAYWRIGHT=false"],
            cwd=agent_dir / "openhands-repo",
            log_path=log,
        )
        marker.write_text("ready\n")

    def grade(self, spec: ResolvedSpec, run_dir: Path, cache_root: Path) -> None:
        grade_path = run_dir / "artifacts" / "cybergym" / "grades.json"
        if not grade_path.is_file():
            raise StageError("CyberGym native harness did not produce grades.json")
        grades = json.loads(grade_path.read_text())
        ids = _ids(run_dir, spec)
        if set(grades) != set(ids):
            raise StageError("CyberGym grades do not match the pool")
        if spec.benchmark.sample_size == 1:
            self._write_gate(cache_root, "canary", spec, grades)
        elif spec.benchmark.sample_size == 10:
            self._write_gate(cache_root, "smoke", spec, grades)

    @staticmethod
    def _write_gate(cache_root: Path, name: str, spec: ResolvedSpec, grades: dict) -> None:
        if any(item.get("infrastructure_error") for item in grades.values()):
            return
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
