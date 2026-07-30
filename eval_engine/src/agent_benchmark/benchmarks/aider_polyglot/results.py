from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.result import TaskResult


def pool_ids(spec: ResolvedSpec, run_dir: Path) -> list[str]:
    payload = json.loads((run_dir / spec.benchmark.pool_path).read_text())
    return [str(task_id) for task_id in payload["instance_ids"]]


def native_root(spec: ResolvedSpec, run_dir: Path) -> Path:
    return run_dir / "artifacts" / "aider_native" / spec.run_id


def result_paths(spec: ResolvedSpec, run_dir: Path) -> list[Path]:
    return sorted(native_root(spec, run_dir).glob("*/exercises/practice/*/.aider.results.json"))


def task_id(path: Path) -> str:
    parts = path.parts
    practice = parts.index("practice")
    language = parts[practice - 2]
    return f"{language}/{parts[practice + 1]}"


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _integer(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def model_call_failed(raw: dict[str, Any]) -> bool:
    error_outputs = _integer(raw.get("num_error_outputs")) or 0
    prompt_tokens = _integer(raw.get("prompt_tokens")) or 0
    completion_tokens = _integer(raw.get("completion_tokens")) or 0
    return error_outputs > 0 and prompt_tokens == 0 and completion_tokens == 0


def _outcomes(raw: dict[str, Any]) -> tuple[list[bool], bool, bool]:
    values = raw.get("tests_outcomes")
    if isinstance(values, list):
        outcomes = [value for value in values if isinstance(value, bool)]
    else:
        outcomes = []
    pass_at_1 = bool(outcomes and outcomes[0])
    pass_at_2 = any(outcomes[:2])
    return outcomes, pass_at_1, pass_at_2


def normalize(spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
    expected = pool_ids(spec, run_dir)
    by_id: dict[str, TaskResult] = {}
    for path in result_paths(spec, run_dir):
        identity = task_id(path)
        if identity not in expected:
            continue
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if identity in by_id:
            raise StageError(f"duplicate Aider Polyglot result for {identity}")
        exception = raw.get("exception")
        call_failed = model_call_failed(raw)
        outcomes, pass_at_1, pass_at_2 = _outcomes(raw)
        malformed = _integer(raw.get("num_malformed_responses"))
        by_id[identity] = TaskResult(
            run_id=spec.run_id,
            task_id=identity,
            status="error" if exception or call_failed else "completed",
            metrics={
                "resolved": pass_at_2 and not exception and not call_failed,
                "pass_at_1": pass_at_1 and not exception and not call_failed,
                "pass_at_2": pass_at_2 and not exception and not call_failed,
                "attempts_executed": len(outcomes),
                "edit_format_well_formed": malformed == 0 if malformed is not None else None,
                "malformed_responses": malformed,
                "model_error_outputs": _integer(raw.get("num_error_outputs")),
                "test_timeouts": _integer(raw.get("test_timeouts")),
                "syntax_errors": _integer(raw.get("syntax_errors")),
                "indentation_errors": _integer(raw.get("indentation_errors")),
            },
            cost_usd=_number(raw.get("cost")),
            input_tokens=_integer(raw.get("prompt_tokens")),
            output_tokens=_integer(raw.get("completion_tokens")),
            cached_tokens=None,
            duration_seconds=_number(raw.get("duration")),
            error_type=(
                "AiderTaskException"
                if exception
                else "AiderModelCallError"
                if call_failed
                else None
            ),
            raw_artifacts=[str(path.relative_to(run_dir))],
        )
    return [
        by_id.get(identity)
        or TaskResult(
            run_id=spec.run_id,
            task_id=identity,
            status="missing",
            metrics={"resolved": False, "pass_at_1": False, "pass_at_2": False},
            error_type="MissingAiderResult",
        )
        for identity in expected
    ]


def validate_and_summarize(spec: ResolvedSpec, run_dir: Path) -> dict[str, object]:
    expected = set(pool_ids(spec, run_dir))
    seen: set[str] = set()
    completed = 0
    pass_1 = pass_2 = 0
    for path in result_paths(spec, run_dir):
        identity = task_id(path)
        if identity not in expected:
            raise StageError(f"unknown Aider Polyglot result: {identity}")
        if identity in seen:
            raise StageError(f"duplicate Aider Polyglot result for {identity}")
        seen.add(identity)
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise StageError(f"malformed Aider result: {identity}") from error
        if model_call_failed(raw):
            raise StageError(
                f"Aider model calls failed for {identity}: error outputs with zero tokens"
            )
        if raw.get("exception"):
            completed += 1
            continue
        outcomes, first, second = _outcomes(raw)
        if not outcomes:
            raise StageError(f"Aider result has no test outcomes: {identity}")
        if (
            raw.get("commit_hash", "").split("-", 1)[0]
            != str(spec.benchmark.settings["runner_revision"])[:7]
        ):
            raise StageError(f"Aider runner revision mismatch in result: {identity}")
        if raw.get("edit_format") != spec.benchmark.settings["edit_format"]:
            raise StageError(f"Aider edit format mismatch in result: {identity}")
        completed += 1
        pass_1 += first
        pass_2 += second
    if seen != expected:
        missing = ", ".join(sorted(expected - seen)[:3])
        raise StageError(f"Aider Polyglot results are incomplete; missing: {missing}")
    summary = {
        "task_count": len(expected),
        "completed_count": completed,
        "pass_num_1": pass_1,
        "pass_num_2": pass_2,
        "official_pass_rate_1": pass_1 / completed if completed else None,
        "official_pass_rate_2": pass_2 / completed if completed else None,
        "pool_pass_rate_1": pass_1 / len(expected) if expected else None,
        "pool_pass_rate_2": pass_2 / len(expected) if expected else None,
        "officially_comparable": len(expected) == 225 and completed == 225,
    }
    destination = run_dir / "artifacts" / "aider_native_summary.json"
    destination.write_text(json.dumps(summary, indent=2) + "\n")
    return summary
