"""Durable per-task grade storage for the CyberGym harness.

CyberGym grades each task inside the execute stage, but the aggregate ``grades.json`` used to be
written only after the whole thread pool drained. A process killed mid-run therefore lost the grade
of every task that had already finished, even though its raw evidence was on disk -- which is what
forced the hand-written recovery scripts under ``runs/glm53-cybergym-friendli-FINAL/``.

Each grade is now appended to ``grades.jsonl`` the moment it is produced. ``grades.json`` stays the
file every downstream consumer reads; :func:`fold_grades` rebuilds it from the ledger so a stopped
run can still be graded and reported.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

GRADES_JSON = "grades.json"
GRADES_JSONL = "grades.jsonl"

_append_lock = threading.Lock()


def append_grade(out: Path, task_id: str, grade: dict) -> None:
    """Append one task's grade to the durable ledger. Never raises."""
    try:
        out.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"task_id": task_id, "grade": grade}, default=str)
        with _append_lock, (out / GRADES_JSONL).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        # A ledger write must never take down a task that otherwise succeeded; the aggregate
        # written at the end of execute remains the primary record for a clean run.
        pass


def read_ledger(out: Path) -> dict[str, dict]:
    """Return every grade recorded in the ledger, last write per task winning."""
    path = out / GRADES_JSONL
    if not path.is_file():
        return {}
    grades: dict[str, dict] = {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a torn final line after a hard kill
        task_id = record.get("task_id")
        if isinstance(task_id, str) and isinstance(record.get("grade"), dict):
            grades[task_id] = record["grade"]
    return grades


def write_grades(out: Path, grades: dict[str, dict]) -> None:
    """Write ``grades.json`` atomically so a crash cannot leave it half-written."""
    out.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(grades, indent=2, default=str) + "\n"
    fd, temporary = tempfile.mkstemp(dir=out, prefix=".grades-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, out / GRADES_JSON)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def fold_grades(out: Path) -> dict[str, dict]:
    """Merge the ledger into ``grades.json`` and return the result.

    Used when grading a run that was stopped before execute finished. Entries already in
    ``grades.json`` win, since they come from a completed execute stage.
    """
    existing: dict[str, dict] = {}
    path = out / GRADES_JSON
    if path.is_file():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                existing = loaded
        except ValueError:
            existing = {}
    merged = {**read_ledger(out), **existing}
    if merged:
        write_grades(out, merged)
    return merged
