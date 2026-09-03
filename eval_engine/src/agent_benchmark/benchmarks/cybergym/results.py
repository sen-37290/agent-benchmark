from __future__ import annotations

import json
from pathlib import Path

from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.harnesses.cybergym_grades import fold_grades
from agent_benchmark.run.result import TaskResult
from agent_benchmark.run.stop import stop_reason


def normalize(spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
    out = run_dir / "artifacts" / "cybergym"
    # Fold the append-only ledger in first: after a mid-run stop it holds grades for tasks that
    # finished before the aggregate was written, which would otherwise all read as missing.
    grades = fold_grades(out)
    # An absent grade is a defect unless the run was deliberately stopped before that task
    # started, in which case it never ran and must not count as a failure.
    stopped = stop_reason(run_dir)
    absent_status = "unrun" if stopped else "missing"
    absent_error = None if stopped else "MissingGrade"
    results: list[TaskResult] = []
    for task_id in json.loads((run_dir / spec.benchmark.pool_path).read_text())["instance_ids"]:
        item = grades.get(task_id)
        if item is None:
            results.append(
                TaskResult(
                    run_id=spec.run_id,
                    task_id=task_id,
                    status=absent_status,
                    error_type=absent_error,
                )
            )
            continue
        resolved = bool(item.get("vul_exit_code", 0) != 0 and item.get("fix_exit_code", 0) == 0)
        status = "error" if item.get("infrastructure_error") else "completed"
        results.append(
            TaskResult(
                run_id=spec.run_id,
                task_id=task_id,
                status=status,
                metrics={
                    "resolved": resolved,
                    "reward": 1 if resolved else 0,
                    "vul_exit_code": item.get("vul_exit_code"),
                    "fix_exit_code": item.get("fix_exit_code"),
                    "final_poc_present": bool(item.get("final_poc_present")),
                    "candidate_count": item.get("candidate_count", 0),
                },
                cost_usd=item.get("cost_usd"),
                input_tokens=item.get("input_tokens"),
                output_tokens=item.get("output_tokens"),
                cached_tokens=item.get("cache_read_tokens"),
                llm_requests=item.get("llm_requests"),
                duration_seconds=item.get("duration_seconds"),
                error_type=item.get("error_type"),
                raw_artifacts=list(item.get("raw_artifacts", [])),
            )
        )
    return results
