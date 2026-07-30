import json
from pathlib import Path

import pytest

from agent_benchmark.benchmarks.aider_polyglot.pool import create_pool
from agent_benchmark.benchmarks.aider_polyglot.results import normalize, validate_and_summarize
from agent_benchmark.config.loader import resolve
from agent_benchmark.config.schema import UserRequest
from agent_benchmark.exceptions import StageError

ROOT = Path(__file__).parents[1]


def setup_run(tmp_path: Path):
    pool = tmp_path / "pool-source.json"
    create_pool(pool, "language", 3)
    spec = resolve(
        UserRequest(
            benchmark="aider-polyglot",
            sampling="language",
            size=3,
            model="glm-5.2",
            reasoning_effort="xhigh",
            provider="openrouter",
            workers=1,
            budget_usd=10,
        ),
        "test-aider-results",
        ROOT,
        pool,
    )
    run_dir = tmp_path / "run"
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / spec.benchmark.pool_path).write_text(pool.read_text())
    ids = json.loads(pool.read_text())["instance_ids"]
    return spec, run_dir, ids


def write_result(
    spec,
    run_dir: Path,
    identity: str,
    outcomes: list[bool],
    *,
    cost: float | None = 0.25,
    exception: str | None = None,
    model_error: bool = False,
) -> Path:
    language, slug = identity.split("/", 1)
    task = (
        run_dir
        / "artifacts"
        / "aider_native"
        / spec.run_id
        / language
        / "exercises"
        / "practice"
        / slug
    )
    task.mkdir(parents=True)
    raw = {
        "testcase": slug,
        "model": "openrouter/z-ai/glm-5.2",
        "edit_format": "diff",
        "tests_outcomes": outcomes,
        "duration": 12.5,
        "commit_hash": "a4be6cc",
        "num_malformed_responses": 0,
        "test_timeouts": 0,
        "syntax_errors": 0,
        "indentation_errors": 0,
        "prompt_tokens": 100,
        "completion_tokens": 20,
    }
    if model_error:
        raw.update(num_error_outputs=2, prompt_tokens=0, completion_tokens=0, cost=0.0)
    if cost is not None:
        raw["cost"] = cost
    if exception:
        raw["exception"] = exception
    destination = task / ".aider.results.json"
    destination.write_text(json.dumps(raw))
    return destination


def test_normalizes_attempts_cost_tokens_and_missing(tmp_path: Path) -> None:
    spec, run_dir, ids = setup_run(tmp_path)
    write_result(spec, run_dir, ids[0], [True], cost=0.0)
    write_result(spec, run_dir, ids[1], [False, True], cost=None)

    results = normalize(spec, run_dir)
    assert [result.status for result in results] == ["completed", "completed", "missing"]
    assert results[0].metrics["pass_at_1"] is True
    assert results[0].metrics["pass_at_2"] is True
    assert results[0].cost_usd == 0.0
    assert results[1].metrics["pass_at_1"] is False
    assert results[1].metrics["pass_at_2"] is True
    assert results[1].cost_usd is None
    assert results[1].cached_tokens is None
    assert results[2].metrics["resolved"] is False


def test_grade_validates_without_rerunning_tests(tmp_path: Path) -> None:
    spec, run_dir, ids = setup_run(tmp_path)
    write_result(spec, run_dir, ids[0], [True])
    write_result(spec, run_dir, ids[1], [False, True])
    write_result(spec, run_dir, ids[2], [False, False])

    summary = validate_and_summarize(spec, run_dir)
    assert summary["pass_num_1"] == 1
    assert summary["pass_num_2"] == 2
    assert summary["official_pass_rate_2"] == pytest.approx(2 / 3)
    assert summary["pool_pass_rate_2"] == pytest.approx(2 / 3)
    assert summary["officially_comparable"] is False


def test_grade_rejects_missing_and_malformed_results(tmp_path: Path) -> None:
    spec, run_dir, ids = setup_run(tmp_path)
    write_result(spec, run_dir, ids[0], [True])
    malformed = write_result(spec, run_dir, ids[1], [False])
    malformed.write_text("{")

    with pytest.raises(StageError, match="malformed"):
        validate_and_summarize(spec, run_dir)


def test_exception_is_an_error_result(tmp_path: Path) -> None:
    spec, run_dir, ids = setup_run(tmp_path)
    write_result(spec, run_dir, ids[0], [], exception="traceback")
    result = normalize(spec, run_dir)[0]
    assert result.status == "error"
    assert result.error_type == "AiderTaskException"
    assert result.metrics["resolved"] is False


def test_model_call_failure_is_not_normalized_as_a_zero_score(tmp_path: Path) -> None:
    spec, run_dir, ids = setup_run(tmp_path)
    write_result(spec, run_dir, ids[0], [False, False], model_error=True)

    result = normalize(spec, run_dir)[0]

    assert result.status == "error"
    assert result.error_type == "AiderModelCallError"
    assert result.metrics["model_error_outputs"] == 2
    assert result.metrics["resolved"] is False
    with pytest.raises(StageError, match="model calls failed"):
        validate_and_summarize(spec, run_dir)
