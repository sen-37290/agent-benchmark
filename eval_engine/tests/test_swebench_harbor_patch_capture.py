from pathlib import Path

from agent_benchmark.benchmarks.swebench_verified.benchmark import HARBOR_TEMPLATE_VERSION

ASSETS = (
    Path(__file__).parents[1]
    / "src"
    / "agent_benchmark"
    / "benchmarks"
    / "swebench_verified"
    / "assets"
)


def test_harbor_captures_patch_from_agent_start_commit() -> None:
    dockerfile = (ASSETS / "environment" / "Dockerfile").read_text()
    verifier = (ASSETS / "tests" / "test.sh").read_text()

    assert "git rev-parse HEAD > /opt/agent-benchmark-start-commit" in dockerfile
    assert 'AGENT_START_COMMIT="$(cat /opt/agent-benchmark-start-commit' in verifier
    assert 'git diff --cached --no-color --binary "$AGENT_START_COMMIT"' in verifier
    assert 'git diff --cached --no-color --binary "$BASE_COMMIT"' not in verifier
    assert HARBOR_TEMPLATE_VERSION == "2"
