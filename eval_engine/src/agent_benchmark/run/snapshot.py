"""A small, cheap status object for one run.

The fleet monitor polls ten VMs over SSH, so what it reads has to be tiny and quick to produce.
``state.json`` only carries the eight coarse pipeline stages -- it cannot answer "how many of the
500 tasks are done and what have they cost". This module derives that from the artifacts each
harness already writes, so nothing new has to be plumbed through the harnesses themselves.

Sources, in order of preference:

* ``results/task_results.jsonl`` -- present once normalize has run; authoritative.
* the live harness output -- CyberGym's ``grades.jsonl`` ledger, Harbor's per-trial
  ``result.json`` files, mini-swe-agent's per-task trajectories. This is what makes progress
  visible *during* the execute stage, which is where a run spends nearly all of its time.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_benchmark.run.state import StageName, StageStatus
from agent_benchmark.run.stop import stop_reason
from agent_benchmark.run.store import RunStore


@dataclass
class Snapshot:
    label: str | None
    run_id: str
    benchmark: str
    model: str
    stage: str
    stage_status: str
    total: int
    done: int
    running: int
    failed: int
    unrun: int
    resolved: int
    cost_usd: float | None
    cost_complete: bool
    per_task_cap_usd: float
    experiment_cap_usd: float | None
    pct_budget: float | None
    last_task: str | None
    stopped_reason: str | None
    cancelled: bool
    results_path: str
    errors: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue  # a torn final line while the file is being appended to
    return records


def _from_task_results(run_dir: Path) -> tuple[dict[str, str], float, bool, int] | None:
    """Per-task status from the normalized results, once they exist."""
    records = _read_jsonl(run_dir / "results" / "task_results.jsonl")
    if not records:
        return None
    statuses: dict[str, str] = {}
    cost = 0.0
    complete = True
    resolved = 0
    for record in records:
        task_id = record.get("task_id")
        if not isinstance(task_id, str):
            continue
        statuses[task_id] = str(record.get("status", "missing"))
        value = record.get("cost_usd")
        if isinstance(value, int | float):
            cost += float(value)
        elif statuses[task_id] == "completed":
            complete = False
        metrics = record.get("metrics") or {}
        if isinstance(metrics, dict) and metrics.get("resolved"):
            resolved += 1
    return statuses, cost, complete, resolved


def _from_cybergym(run_dir: Path) -> tuple[dict[str, str], float, bool, int]:
    statuses: dict[str, str] = {}
    cost = 0.0
    complete = True
    resolved = 0
    for record in _read_jsonl(run_dir / "artifacts" / "cybergym" / "grades.jsonl"):
        task_id = record.get("task_id")
        grade = record.get("grade")
        if not isinstance(task_id, str) or not isinstance(grade, dict):
            continue
        statuses[task_id] = "error" if grade.get("infrastructure_error") else "completed"
        value = grade.get("cost_usd")
        if isinstance(value, int | float):
            cost += float(value)
        if not grade.get("cost_complete", False):
            complete = False
        if grade.get("vul_exit_code", 0) != 0 and grade.get("fix_exit_code", 0) == 0:
            resolved += 1
    return statuses, cost, complete, resolved


def _from_harbor(run_dir: Path) -> tuple[dict[str, str], float, bool, int]:
    statuses: dict[str, str] = {}
    cost = 0.0
    resolved = 0
    roots = [run_dir / "artifacts" / "harbor_jobs", run_dir / "artifacts" / "retry_attempts"]
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("**/result.json"):
            try:
                raw = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            name = raw.get("task_name")
            if not isinstance(name, str):
                continue
            task_id = name.rsplit("/", 1)[-1]
            statuses[task_id] = "error" if raw.get("exception_info") else "completed"
            agent = raw.get("agent_result")
            if isinstance(agent, dict) and isinstance(agent.get("cost_usd"), int | float):
                cost += float(agent["cost_usd"])
            verifier = raw.get("verifier_result")
            rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
            reward = rewards.get("reward") if isinstance(rewards, dict) else None
            if isinstance(reward, int | float) and reward > 0:
                resolved += 1
    return statuses, cost, True, resolved


def _from_mini_swe_agent(run_dir: Path) -> tuple[dict[str, str], float, bool, int]:
    statuses: dict[str, str] = {}
    cost = 0.0
    resolved = 0
    # During a run the trajectories live under the per-attempt retry directories; the canonical
    # minisweagent_swebench/ tree is only populated at the very end of execute. Read both, or a
    # 500-task run shows 0 done for hours. Later attempts win, matching attempt selection.
    seen_paths: dict[str, Path] = {}
    for root in (
        run_dir / "artifacts" / "retry_attempts" / "mini_swe_agent_native",
        run_dir / "artifacts" / "minisweagent_swebench",
    ):
        if not root.exists():
            continue
        for path in sorted(root.glob("**/*.traj.json")):
            seen_paths[path.name] = path
    for path in seen_paths.values():
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        task_id = path.name.removesuffix(".traj.json")
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        exit_status = info.get("exit_status")
        statuses[task_id] = "completed" if exit_status else "error"
        stats = info.get("model_stats") if isinstance(info.get("model_stats"), dict) else {}
        value = stats.get("instance_cost")
        if isinstance(value, int | float):
            cost += float(value)
    # Resolution is only known after the official grader runs; report it from its summary.
    summary = run_dir / "artifacts" / "official_summary.json"
    if summary.is_file():
        try:
            raw = json.loads(summary.read_text())
            ids = raw.get("resolved_ids")
            resolved = (
                len(ids) if isinstance(ids, list) else int(raw.get("resolved_instances") or 0)
            )
        except (OSError, ValueError, TypeError):
            resolved = 0
    return statuses, cost, True, resolved


_LIVE_READERS = {
    "cybergym-native": _from_cybergym,
    "harbor": _from_harbor,
    "mini-swe-agent-native": _from_mini_swe_agent,
}


def _artifact_root(spec, run_dir: Path) -> Path:
    """Where this run's harness artifacts currently live.

    Before the collect stage they exist only in the execution workspace; afterwards the run bundle
    holds a verified copy. Prefer the bundle once it has artifacts so a finished run is read from
    its authoritative location.
    """
    if (run_dir / "artifacts").is_dir() and any((run_dir / "artifacts").iterdir()):
        return run_dir
    workspace = Path(spec.target.remote_root) / spec.run_id
    if (workspace / "artifacts").is_dir():
        return workspace
    return run_dir


def _last_task(run_dir: Path, known: dict[str, str]) -> str | None:
    """The most recently finished task.

    The CyberGym ledger is append-only, so its last entry is the answer. Otherwise fall back to
    the newest per-task artifact directory by mtime -- a lexicographic max would just return
    whichever task ID sorts last, which says nothing about progress.
    """
    if not known:
        return None
    ledger = _read_jsonl(run_dir / "artifacts" / "cybergym" / "grades.jsonl")
    for record in reversed(ledger):
        task_id = record.get("task_id")
        if isinstance(task_id, str) and task_id in known:
            return task_id
    newest: tuple[float, str] | None = None
    for root in (
        run_dir / "artifacts" / "harbor_jobs",
        run_dir / "artifacts" / "minisweagent_swebench",
    ):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.name not in known:
                continue
            try:
                stamp = child.stat().st_mtime
            except OSError:
                continue
            if newest is None or stamp > newest[0]:
                newest = (stamp, child.name)
    return newest[1] if newest else None


def build(store: RunStore) -> Snapshot:
    spec = store.load_spec()
    state = store.load_state()
    run_dir = store.path

    # Report the stage the run is actually at: the first one still running or failed, else the
    # last one that succeeded.
    stage = "pending"
    stage_status = str(StageStatus.PENDING)
    for name in StageName:
        record = state.stages.get(name)
        if record is None:
            continue
        if record.status in {StageStatus.RUNNING, StageStatus.FAILED}:
            stage, stage_status = str(name), str(record.status)
            break
        if record.status == StageStatus.SUCCEEDED:
            stage, stage_status = str(name), str(record.status)

    ids = json.loads((run_dir / spec.benchmark.pool_path).read_text())["instance_ids"]
    from_results = _from_task_results(run_dir)
    if from_results is not None:
        statuses, cost, cost_complete, resolved = from_results
    else:
        reader = _LIVE_READERS.get(spec.benchmark.harness)
        # Harness artifacts are written into the execution workspace and only land in the run
        # bundle when the collect stage runs. Read the workspace too, or a run in progress reports
        # 0 done and $0.00 for its whole duration -- which is most of its life. The controller is
        # co-located with its executor, so that path is readable from here.
        root = _artifact_root(spec, run_dir)
        statuses, cost, cost_complete, resolved = reader(root) if reader else ({}, 0.0, True, 0)

    known = {task_id: status for task_id, status in statuses.items() if task_id in set(ids)}
    failed = sum(1 for status in known.values() if status == "error")
    unrun = sum(1 for status in known.values() if status == "unrun")
    done = sum(1 for status in known.values() if status in {"completed", "error"})
    errors: dict[str, int] = {}
    for record in _read_jsonl(run_dir / "results" / "task_results.jsonl"):
        error_type = record.get("error_type")
        if isinstance(error_type, str) and error_type:
            errors[error_type] = errors.get(error_type, 0) + 1

    # "running" is inferred: tasks neither finished nor deliberately unrun, capped by the worker
    # pool, since at most `workers` can be in flight at once.
    outstanding = max(0, len(ids) - done - unrun)
    running = 0
    if stage == str(StageName.EXECUTE) and stage_status == str(StageStatus.RUNNING):
        running = min(outstanding, spec.execution.workers)

    cap = spec.budget.total_usd
    return Snapshot(
        label=spec.label,
        run_id=spec.run_id,
        benchmark=spec.benchmark.profile,
        model=spec.model.profile,
        stage=stage,
        stage_status=stage_status,
        total=len(ids),
        done=done,
        running=running,
        failed=failed,
        unrun=unrun,
        resolved=resolved,
        cost_usd=round(cost, 4) if statuses else None,
        cost_complete=cost_complete,
        per_task_cap_usd=spec.budget.per_task_usd,
        experiment_cap_usd=cap,
        pct_budget=round(100 * cost / cap, 1) if cap else None,
        last_task=_last_task(_artifact_root(spec, run_dir), known),
        stopped_reason=stop_reason(run_dir),
        cancelled=state.cancelled,
        results_path=str(run_dir),
        errors=errors,
    )
