"""Cooperative stop signalling for a run.

An experiment can be stopped for two reasons that are not failures: it reached its total cost cap,
or an operator asked it to wind down. Both must end the run *cleanly* -- stop starting new tasks,
let in-flight tasks finish, then grade and report whatever completed. Killing the batch instead
(the previous behaviour of the budget watchdog) destroys exactly the evidence the run exists to
produce.

The signal is a small JSON file in the run directory so it survives a restart, can be read by the
grade stage long after execute exited, and can be dropped in by an operator over SSH.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

STOP_FILENAME = "STOP"

#: The run reached its configured total cost cap.
REASON_COST_CAP = "experiment_cost_cap"
#: An operator asked the run to wind down.
REASON_OPERATOR = "operator_stop"


def stop_path(run_dir: Path) -> Path:
    return run_dir / STOP_FILENAME


def request_stop(run_dir: Path, reason: str, detail: str = "") -> None:
    """Ask the run to stop starting new work. Idempotent: the first reason recorded wins."""
    path = stop_path(run_dir)
    if path.exists():
        return
    payload = json.dumps(
        {"reason": reason, "detail": detail, "requested_at": time.time()}, indent=2
    )
    fd, temporary = tempfile.mkstemp(dir=run_dir, prefix=".stop-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def stop_reason(run_dir: Path) -> str | None:
    """The recorded stop reason, or None when the run was never asked to stop."""
    path = stop_path(run_dir)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        # The file exists, so a stop was definitely requested; the reason is just unreadable.
        return REASON_OPERATOR
    reason = record.get("reason")
    return reason if isinstance(reason, str) and reason else REASON_OPERATOR


def should_stop(run_dir: Path) -> bool:
    """True once the run has been asked to stop dispatching new tasks."""
    return stop_path(run_dir).is_file()
