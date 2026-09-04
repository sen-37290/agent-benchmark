from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from agent_benchmark.run.costguard import COST_LIMIT_MARKER

TRANSIENT_TYPES = frozenset(
    {
        "APIConnectionError",
        "APIError",
        "ApiConnectionClosedError",
        "AgentConnectionError",
        "AgentRateLimitError",
        "InternalServerError",
        "OpenRouterAPIError",
        "OpenRouterRateLimitError",
        "RateLimitError",
        "ServiceUnavailableError",
        # litellm raises a bare `Timeout` for a read/connect timeout. It was absent here, and its
        # message ("Connection timed out") matched no text marker either, so timed-out tasks were
        # scored as model failures instead of being retried.
        "Timeout",
        "APITimeoutError",
    }
)
NON_RETRYABLE_TYPES = frozenset(
    {
        "AgentAuthenticationError",
        "AgentTimeoutError",
        "ApiUsageLimitError",
        "ModelNotFoundError",
        "OpenRouterAuthenticationError",
        "VerifierTimeoutError",
        # litellm's own names, which Harbor's retry policy also refuses. A bad key or an
        # oversized context does not heal with time; retrying only burns the wall clock.
        "AuthenticationError",
        "PermissionDeniedError",
        "ContextLengthExceededError",
        "ContextWindowExceededError",
        "OutputLengthExceededError",
        # A task stopped by its own dollar cap must never be retried: each attempt gets a fresh
        # cost meter, so retrying would spend the cap again. Checked before TRANSIENT_TYPES.
        "CostLimitExceeded",
    }
)
TRANSIENT_TEXT = (
    "connection closed",
    "connection reset",
    "connection error",
    "rate limit",
    "request failed",
    "stream closed",
    "temporarily unavailable",
    "timed out connecting",
    "connection timed out",
    "read timed out",
    "overloaded",
    # A billing outage is infrastructure, not a model failure. The rejected request is never
    # billed, so retrying it is free, and the backoff gives a topped-up balance time to land.
    "credit balance",
    "internal server error",
    "http 408",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "error response from daemon",
    "failed to pull",
    "pull access denied",
    "toomanyrequests",
)


def is_transient(error_type: str | None, message: str | None = None) -> bool:
    # The cost cap is authoritative however it is reported. Harbor may surface the guard's stop
    # wrapped in another exception type, so match the marker in the message too -- otherwise a
    # text marker below (or a generic type) could re-run a task that already spent its cap.
    if message and COST_LIMIT_MARKER in message:
        return False
    if not error_type or error_type in NON_RETRYABLE_TYPES:
        return False
    if error_type in TRANSIENT_TYPES and error_type != "OpenRouterAPIError":
        return True
    text = f"{error_type} {message or ''}".lower()
    return any(marker in text for marker in TRANSIENT_TEXT)


def retry_delay(attempt: int, *, minimum: float = 30, maximum: float = 240) -> float:
    return min(maximum, minimum * (2 ** max(0, attempt - 1)))


def manifest_path(run_dir: Path) -> Path:
    return run_dir / "artifacts" / "retry_attempts" / "manifest.json"


def load_manifest(run_dir: Path, task_ids: list[str]) -> dict[str, Any]:
    path = manifest_path(run_dir)
    if path.is_file():
        return json.loads(path.read_text())
    return {
        "version": 1,
        "tasks": {task_id: {"attempts": [], "selected_attempt": None} for task_id in task_ids},
    }


def save_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    path = manifest_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".manifest.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record_attempt(
    manifest: dict[str, Any],
    task_id: str,
    *,
    attempt: int,
    artifact: str,
    status: str,
    error_type: str | None,
    retryable: bool,
    cost_usd: float | None,
) -> None:
    task = manifest["tasks"][task_id]
    task["attempts"] = [item for item in task["attempts"] if item["attempt"] != attempt]
    task["attempts"].append(
        {
            "attempt": attempt,
            "artifact": artifact,
            "status": status,
            "error_type": error_type,
            "retryable": retryable,
            "cost_usd": cost_usd,
        }
    )
    task["attempts"].sort(key=lambda item: item["attempt"])


def select_attempt(manifest: dict[str, Any], task_id: str, attempt: int) -> None:
    manifest["tasks"][task_id]["selected_attempt"] = attempt


def pending_tasks(manifest: dict[str, Any], max_attempts: int) -> list[str]:
    pending: list[str] = []
    for task_id, task in manifest["tasks"].items():
        if task["selected_attempt"] is not None:
            continue
        attempts = task["attempts"]
        if not attempts or (attempts[-1]["retryable"] and len(attempts) < max_attempts):
            pending.append(task_id)
        elif attempts:
            task["selected_attempt"] = attempts[-1]["attempt"]
    return sorted(pending)


def wait_before_attempt(attempt: int) -> None:
    if attempt > 1:
        time.sleep(retry_delay(attempt - 1))


def attempt_costs(run_dir: Path, task_id: str) -> tuple[int, float | None, float | None, bool]:
    manifest = load_manifest(run_dir, [])
    task = manifest.get("tasks", {}).get(task_id, {})
    attempts = task.get("attempts", [])
    if not attempts:
        return 1, None, 0.0, True
    selected = task.get("selected_attempt")
    final = next(
        (
            float(item["cost_usd"])
            for item in attempts
            if item["attempt"] == selected and item.get("cost_usd") is not None
        ),
        None,
    )
    overhead_values = [
        float(item["cost_usd"])
        for item in attempts
        if item["attempt"] != selected and item.get("cost_usd") is not None
    ]
    complete = all(item.get("cost_usd") is not None for item in attempts)
    return len(attempts), final, sum(overhead_values) if overhead_values else 0.0, complete


def attempt_exhausted(run_dir: Path, task_id: str) -> bool:
    manifest = load_manifest(run_dir, [])
    task = manifest.get("tasks", {}).get(task_id, {})
    selected = task.get("selected_attempt")
    item = next((entry for entry in task.get("attempts", []) if entry["attempt"] == selected), None)
    return bool(item and item["status"] == "error" and item["retryable"])
