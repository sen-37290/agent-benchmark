from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_benchmark.backends.ssh import SSHBackend
from agent_benchmark.models import StageName, StageStatus
from agent_benchmark.registry import benchmark_plugin
from agent_benchmark.reporting import read_results, write_report, write_results
from agent_benchmark.run_store import RunStore


class Pipeline:
    def __init__(self, store: RunStore, project_root: Path):
        self.store = store
        self.spec = store.load_spec()
        self.backend = SSHBackend(self.spec, project_root)

    def run(self) -> None:
        self._stage(StageName.DEPLOY, lambda: self.backend.deploy(self.store.path))
        self._stage(StageName.PREPARE, lambda: self.backend.run_worker("prepare"))
        self._stage(StageName.EXECUTE, lambda: self.backend.run_worker("execute"))
        self._stage(StageName.GRADE, lambda: self.backend.run_worker("grade"))
        self._stage(StageName.COLLECT, lambda: self.backend.collect(self.store.path))
        self._stage(StageName.NORMALIZE, self._normalize)
        self._stage(StageName.REPORT, self._report)
        self._stage(StageName.CLEANUP, self.backend.cleanup)

    def collect(self) -> None:
        self._stage(StageName.COLLECT, lambda: self.backend.collect(self.store.path), force=True)

    def report(self) -> None:
        if not (self.store.path / "results" / "task_results.jsonl").exists():
            self._normalize()
        self._report()

    def _normalize(self) -> None:
        plugin = benchmark_plugin(self.spec.benchmark.plugin)
        write_results(self.store.path, plugin.normalize(self.spec, self.store.path))

    def _report(self) -> None:
        write_report(self.spec, self.store.path, read_results(self.store.path))

    def _stage(self, stage: StageName, action: Callable[[], None], *, force: bool = False) -> None:
        record = self.store.load_state().stages[stage]
        if record.status == StageStatus.SUCCEEDED and not force:
            return
        self.store.start_stage(stage)
        try:
            action()
        except Exception as error:
            self.store.fail_stage(stage, error)
            raise
        self.store.finish_stage(stage)
