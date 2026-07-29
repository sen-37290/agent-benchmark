from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic

from agent_benchmark.benchmarks import benchmark_plugin
from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.run.remote import SSHBackend
from agent_benchmark.run.result import read_results, write_report, write_results
from agent_benchmark.run.state import StageName, StageStatus
from agent_benchmark.run.store import RunStore


class Pipeline:
    def __init__(self, store: RunStore, project_root: Path):
        self.store = store
        self.spec = store.load_spec()
        self.backend = SSHBackend(self.spec, project_root)

    def run(self) -> None:
        if self.store.load_state().cancelled:
            raise ConfigurationError(f"run {self.store.run_id!r} was cancelled; create a new run")
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
            print(f"[{stage.value}] skipped (already succeeded)", flush=True)
            return
        started = monotonic()
        print(f"[{stage.value}] started", flush=True)
        self.store.start_stage(stage)
        try:
            action()
        except Exception as error:
            self.store.fail_stage(stage, error)
            print(f"[{stage.value}] failed: {type(error).__name__}: {error}", flush=True)
            raise
        self.store.finish_stage(stage)
        print(f"[{stage.value}] succeeded ({monotonic() - started:.1f}s)", flush=True)
