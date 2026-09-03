from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic

from agent_benchmark.benchmarks import benchmark_plugin
from agent_benchmark.exceptions import ConfigurationError, StageError
from agent_benchmark.run.remote import SSHBackend
from agent_benchmark.run.result import read_results, write_report, write_results
from agent_benchmark.run.retry import wait_before_attempt
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
        retries = self.spec.execution.error_retries
        self._stage(
            StageName.DEPLOY,
            lambda: self.backend.deploy(self.store.path),
            error_retries=retries,
        )
        self._stage(
            StageName.PREPARE,
            lambda: self.backend.run_worker("prepare"),
            error_retries=retries,
        )
        self._stage(StageName.EXECUTE, lambda: self.backend.run_worker("execute"))
        # The budget watchdog records its stop inside the remote worker; grading and normalize
        # both need to see it, or a deliberately partial pool reads as an incomplete one.
        self.backend.sync_stop(self.store.path)
        self._stage(
            StageName.GRADE,
            lambda: self.backend.run_worker("grade"),
            error_retries=retries,
        )
        self._stage(
            StageName.COLLECT,
            lambda: self.backend.collect(self.store.path),
            error_retries=retries,
        )
        self._stage(StageName.NORMALIZE, self._normalize)
        self._stage(StageName.REPORT, self._report)
        self._stage(StageName.CLEANUP, self.backend.cleanup, error_retries=retries)

    def finalize(self) -> None:
        """Grade, collect, normalize and report whatever exists, however the run ended.

        This is what an experiment's exit trap calls. It re-runs the tail of the pipeline even
        when those stages already ran or previously failed, so a run stopped at its cost cap, or
        killed outright, still produces graded results and a report instead of leaving the
        evidence stranded on the VM. Cleanup is never part of it.
        """
        # Mirror the stop marker first: a SIGTERM-driven stop is recorded on the controller
        # side, and the remote grade stage must know the pool is partial by design.
        try:
            self.backend.sync_stop(self.store.path)
        except StageError as error:
            print(f"[finalize] could not sync the stop marker: {error}", flush=True)
        for stage, action in (
            (StageName.GRADE, lambda: self.backend.run_worker("grade")),
            (StageName.COLLECT, lambda: self.backend.collect(self.store.path)),
        ):
            try:
                self._stage(stage, action, force=True)
            except StageError as error:
                # Grading may legitimately fail (e.g. nothing ran at all). Keep going so the
                # artifacts that do exist are still collected and reported.
                print(f"[finalize] {stage.value} failed: {error}", flush=True)
        self._stage(StageName.NORMALIZE, self._normalize, force=True)
        self._stage(StageName.REPORT, self._report, force=True)
        # Finalize is the definitive end of a run, so free the VM for the next one. The
        # workspace and every artifact are retained; only the lease is released.
        try:
            self.backend.release_lease()
        except StageError as error:
            print(f"[finalize] could not release the VM lease: {error}", flush=True)

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

    def _stage(
        self,
        stage: StageName,
        action: Callable[[], None],
        *,
        force: bool = False,
        error_retries: int = 0,
    ) -> None:
        record = self.store.load_state().stages[stage]
        if record.status == StageStatus.SUCCEEDED and not force:
            print(f"[{stage.value}] skipped (already succeeded)", flush=True)
            return
        for attempt in range(1, error_retries + 2):
            wait_before_attempt(attempt)
            started = monotonic()
            print(f"[{stage.value}] started (attempt {attempt})", flush=True)
            self.store.start_stage(stage)
            try:
                action()
            except Exception as error:
                self.store.fail_stage(stage, error)
                print(f"[{stage.value}] failed: {type(error).__name__}: {error}", flush=True)
                if not isinstance(error, StageError) or attempt > error_retries:
                    raise
                print(f"[{stage.value}] retrying after infrastructure failure", flush=True)
                continue
            self.store.finish_stage(stage)
            print(f"[{stage.value}] succeeded ({monotonic() - started:.1f}s)", flush=True)
            return
