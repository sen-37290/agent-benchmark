from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent_benchmark.benchmarks.swebench_verified.results import _native_reasoning_audit
from agent_benchmark.benchmarks.terminal_bench.results import _terminal_reasoning_audit
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.pipeline import Pipeline
from agent_benchmark.run.retry import (
    attempt_costs,
    attempt_exhausted,
    is_transient,
    load_manifest,
    pending_tasks,
    record_attempt,
    retry_delay,
    save_manifest,
    select_attempt,
)
from agent_benchmark.run.state import StageName, StageStatus


def request(**overrides):
    values = {
        "benchmark": "terminal-bench-2.1",
        "model": "glm-5.3",
        "provider": "openrouter",
        "workers": 1,
    }
    values.update(overrides)
    return UserRequest(**values)


def test_unlimited_budget_is_explicit_and_mutually_exclusive() -> None:
    assert request(no_budget_limit=True).budget_usd is None
    assert request(budget_usd=5).no_budget_limit is False
    with pytest.raises(ValidationError, match="exactly one"):
        request()
    with pytest.raises(ValidationError, match="exactly one"):
        request(budget_usd=5, no_budget_limit=True)


@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        ("OpenRouterRateLimitError", "rate limited"),
        ("ApiConnectionClosedError", "stream closed"),
        ("OpenRouterAPIError", "HTTP 503: unavailable"),
        ("MissingTrajectory", "connection reset"),
        ("DockerError", "toomanyrequests: Docker Hub pull rate limit"),
    ],
)
def test_transient_provider_failures_are_retryable(error_type: str, message: str) -> None:
    assert is_transient(error_type, message)


@pytest.mark.parametrize(
    "error_type",
    ["AgentAuthenticationError", "AgentTimeoutError", "ModelNotFoundError"],
)
def test_nontransient_failures_are_not_retryable(error_type: str) -> None:
    assert not is_transient(error_type, "HTTP 503")


def test_retry_backoff_is_exponential_and_capped() -> None:
    assert [retry_delay(attempt) for attempt in range(1, 6)] == [30, 60, 120, 240, 240]


def test_safe_pipeline_stage_retries_stage_error(monkeypatch) -> None:
    record = SimpleNamespace(status=StageStatus.PENDING)

    class Store:
        def __init__(self) -> None:
            self.started = 0
            self.failed = 0
            self.finished = 0

        def load_state(self):
            return SimpleNamespace(stages={StageName.GRADE: record})

        def start_stage(self, _stage) -> None:
            self.started += 1

        def fail_stage(self, _stage, _error) -> None:
            self.failed += 1

        def finish_stage(self, _stage) -> None:
            self.finished += 1

    pipeline = Pipeline.__new__(Pipeline)
    pipeline.store = Store()
    waits: list[int] = []
    monkeypatch.setattr("agent_benchmark.run.pipeline.wait_before_attempt", waits.append)
    calls = 0

    def flaky_grade() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise StageError("Docker image pull failed")

    pipeline._stage(StageName.GRADE, flaky_grade, error_retries=3)

    assert calls == 3
    assert waits == [1, 2, 3]
    assert pipeline.store.started == 3
    assert pipeline.store.failed == 2
    assert pipeline.store.finished == 1


def test_reasoning_audit_counts_null_fields_without_hiding_them() -> None:
    trajectory = {
        "messages": [
            {
                "role": "assistant",
                "extra": {
                    "response": {
                        "model": "z-ai/glm-5.3",
                        "provider": "Z.AI",
                        "choices": [{"message": {"reasoning": None, "reasoning_details": None}}],
                    }
                },
            },
            {
                "role": "assistant",
                "extra": {
                    "response": {
                        "model": "z-ai/glm-5.3",
                        "provider": "Z.AI",
                        "choices": [
                            {
                                "message": {
                                    "reasoning": "thinking",
                                    "reasoning_details": [{"type": "reasoning.text"}],
                                }
                            }
                        ],
                    }
                },
            },
        ]
    }

    assert _native_reasoning_audit(trajectory) == {
        "assistant_responses": 2,
        "reasoning_responses": 1,
        "reasoning_details_responses": 1,
        "models": ["z-ai/glm-5.3"],
        "providers": ["Z.AI"],
    }


def test_terminal_reasoning_audit_uses_atif_reasoning_content() -> None:
    assert _terminal_reasoning_audit(
        {
            "agent": {"model_name": "openrouter/z-ai/glm-5.3"},
            "steps": [
                {"source": "user", "message": "task"},
                {
                    "source": "agent",
                    "model_name": "z-ai/glm-5.3",
                    "reasoning_content": "thinking",
                },
                {
                    "source": "agent",
                    "model_name": "z-ai/glm-5.3",
                    "reasoning_content": None,
                },
            ],
        }
    ) == {
        "assistant_responses": 2,
        "reasoning_responses": 1,
        "reasoning_details_responses": 0,
        "models": ["z-ai/glm-5.3"],
        "providers": [],
        "provider_metadata_available": False,
    }


def test_manifest_selects_final_cost_without_failed_attempt_cost(tmp_path: Path) -> None:
    manifest = load_manifest(tmp_path, ["task"])
    record_attempt(
        manifest,
        "task",
        attempt=1,
        artifact="artifacts/retry_attempts/attempt-01",
        status="error",
        error_type="OpenRouterRateLimitError",
        retryable=True,
        cost_usd=0.4,
    )
    assert pending_tasks(manifest, 4) == ["task"]
    record_attempt(
        manifest,
        "task",
        attempt=2,
        artifact="artifacts/retry_attempts/attempt-02",
        status="completed",
        error_type=None,
        retryable=False,
        cost_usd=0.6,
    )
    select_attempt(manifest, "task", 2)
    save_manifest(tmp_path, manifest)

    assert pending_tasks(manifest, 4) == []
    assert attempt_costs(tmp_path, "task") == (2, 0.6, 0.4, True)
    assert not attempt_exhausted(tmp_path, "task")


def test_manifest_marks_last_retryable_failure_exhausted(tmp_path: Path) -> None:
    manifest = load_manifest(tmp_path, ["task"])
    for attempt in range(1, 5):
        record_attempt(
            manifest,
            "task",
            attempt=attempt,
            artifact=f"artifacts/retry_attempts/attempt-{attempt:02d}",
            status="error",
            error_type="ApiConnectionClosedError",
            retryable=True,
            cost_usd=0.1,
        )
    assert pending_tasks(manifest, 4) == []
    save_manifest(tmp_path, manifest)

    assert manifest["tasks"]["task"]["selected_attempt"] == 4
    assert attempt_costs(tmp_path, "task") == (4, 0.1, pytest.approx(0.3), True)
    assert attempt_exhausted(tmp_path, "task")
