"""Per-task cost accounting and enforcement shared by the subject-agent bootstraps.

Neither Terminus 2 nor OpenHands offers a per-task dollar limit, so the engine installs its own
meter inside the agent process. Both bootstraps configure it through the environment so the
enforcement point stays inside the process that actually issues the LLM calls:

``AGENT_BENCH_PER_TASK_COST_LIMIT_USD``
    Stop the task once cumulative spend reaches this value. Unset or ``0`` disables enforcement.
``AGENT_BENCH_USAGE_LOG``
    Optional JSONL path; one record is appended per priced response.
``AGENT_BENCH_TASK_ID``
    Recorded in each usage record so a merged ledger stays attributable.

The meter is deliberately fail-open on pricing: a response it cannot price contributes ``0`` and is
recorded with ``priced: false`` rather than aborting the task. It is fail-closed on the limit: once
the threshold is crossed the next call raises, which ends that task and only that task.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

LIMIT_ENV = "AGENT_BENCH_PER_TASK_COST_LIMIT_USD"
USAGE_LOG_ENV = "AGENT_BENCH_USAGE_LOG"
TASK_ID_ENV = "AGENT_BENCH_TASK_ID"

#: Marker written into the agent log so the harness can classify the stop without parsing prose.
COST_LIMIT_MARKER = "AGENT_BENCH_COST_LIMIT_EXCEEDED"


class CostLimitExceeded(RuntimeError):
    """Raised inside the agent process when a task reaches its per-task dollar limit."""

    def __init__(self, spent_usd: float, limit_usd: float) -> None:
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"{COST_LIMIT_MARKER}: task spent ${spent_usd:.4f} of its ${limit_usd:.2f} limit"
        )


def configured_limit() -> float:
    """Return the per-task limit in USD, or ``0.0`` when enforcement is disabled."""
    raw = os.environ.get(LIMIT_ENV, "").strip()
    if not raw:
        return 0.0
    try:
        limit = float(raw)
    except ValueError:
        return 0.0
    return limit if limit > 0 else 0.0


def response_cost(response: Any) -> float | None:
    """Price a LiteLLM response, or return ``None`` when the cost cannot be established.

    LiteLLM attaches a computed cost to most responses; ``completion_cost`` is the fallback for
    providers that do not. A model missing from LiteLLM's price table yields ``None`` so the caller
    can record the gap instead of silently treating the call as free.
    """
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        cost = hidden.get("response_cost")
        if isinstance(cost, int | float) and cost > 0:
            return float(cost)
    try:
        import litellm

        cost = litellm.completion_cost(completion_response=response)
    except Exception:
        return None
    if isinstance(cost, int | float) and cost > 0:
        return float(cost)
    return None


def _usage_fields(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    fields = {}
    for source, target in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        value = getattr(usage, source, None)
        if isinstance(value, int):
            fields[target] = value
    return fields


class CostMeter:
    """Thread-safe cumulative spend tracker for a single task."""

    def __init__(self, limit_usd: float | None = None, usage_log: Path | None = None) -> None:
        self.limit_usd = configured_limit() if limit_usd is None else limit_usd
        if usage_log is None:
            configured = os.environ.get(USAGE_LOG_ENV, "").strip()
            usage_log = Path(configured) if configured else None
        self.usage_log = usage_log
        self.task_id = os.environ.get(TASK_ID_ENV, "") or None
        self._spent = 0.0
        self._calls = 0
        self._unpriced = 0
        self._lock = threading.Lock()

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent

    def record(self, response: Any, model: str | None = None) -> float:
        """Price and record one response, returning cumulative spend."""
        cost = response_cost(response)
        with self._lock:
            self._calls += 1
            if cost is None:
                self._unpriced += 1
            else:
                self._spent += cost
            spent = self._spent
            calls = self._calls
        self._append(
            {
                "ts": time.time(),
                "task_id": self.task_id,
                "model": model or getattr(response, "model", None),
                "call": calls,
                "cost_usd": cost,
                "priced": cost is not None,
                "cumulative_cost_usd": round(spent, 6),
                **_usage_fields(response),
            }
        )
        return spent

    def enforce(self) -> None:
        """Raise :class:`CostLimitExceeded` when the task has reached its limit."""
        if self.limit_usd <= 0:
            return
        spent = self.spent_usd
        if spent >= self.limit_usd:
            raise CostLimitExceeded(spent, self.limit_usd)

    def record_and_enforce(self, response: Any, model: str | None = None) -> float:
        spent = self.record(response, model)
        self.enforce()
        return spent

    def _append(self, record: dict[str, Any]) -> None:
        if self.usage_log is None:
            return
        try:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            # Losing a ledger line must never end a task that is otherwise healthy.
            pass
