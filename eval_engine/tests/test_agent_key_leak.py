"""Regressions for the provider key leaking into agent-visible surfaces and artifacts.

Terminal-Bench published a real API key in 5 of 712 task directories. The chain was:
``--ae KEY=value`` -> Terminus 2's ``extra_env`` -> the tmux pane's start command -> ``pipe-pane``
-> ``agent/terminus_2.pane`` and ``agent/trajectory.json``. Two independent surfaces came with it:
the key sat on an argv the agent could read with ``ps``, and in the shell environment it could read
with ``env``. It needed a task that happened to introspect one of those, which is why only 5
directories leaked -- the key was available to all of them.

These tests pin both halves of the fix: the key never enters the container, and any copy that
reaches an artifact by some other route is scrubbed before grading, archiving or upload.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_benchmark.agents import agent_adapter
from agent_benchmark.harnesses.harbor import build_command
from agent_benchmark.run.scrub import find_secrets, scrub_text, scrub_tree, secret_spans

from test_terminal_bench_agents import resolved_spec

# Shaped like a real key, and long enough to exercise the wrap-tolerant matcher.
FAKE_KEY = "sk-ant-api03-" + "Xy7z" * 23 + "AA"


def test_container_environment_carries_no_secret(tmp_path: Path) -> None:
    """`environment` crosses into the container; the key must never be in it."""
    spec = resolved_spec(tmp_path, "terminal-bench-2.1")
    invocation = agent_adapter(spec.model.subject_agent).invocation(spec, tmp_path, FAKE_KEY)

    assert invocation.environment == {}
    assert FAKE_KEY not in json.dumps(invocation.environment)
    assert FAKE_KEY not in json.dumps(invocation.kwargs)
    # The host process still needs it, or no inference happens at all.
    assert invocation.process_environment[spec.model.api_key_env] == FAKE_KEY


def test_harbor_command_line_carries_no_secret(tmp_path: Path) -> None:
    """The key must not appear in any argv: Harbor's own, or the tmux command it derives."""
    spec = resolved_spec(tmp_path, "terminal-bench-2.1")
    (tmp_path / "inputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / spec.benchmark.pool_path).write_text(json.dumps({"instance_ids": ["hello-world"]}))
    invocation = agent_adapter(spec.model.subject_agent).invocation(spec, tmp_path, FAKE_KEY)

    command = build_command(spec, tmp_path, tmp_path / "cache", FAKE_KEY, invocation)

    assert not any(FAKE_KEY in argument for argument in command)
    # No --ae at all: that flag is what put the value on the pane's start command.
    assert "--ae" not in command


def test_scrub_finds_a_key_broken_by_line_wrapping() -> None:
    """A 160-column pane splits a long key, so a literal replace misses most copies."""
    wrapped = FAKE_KEY[:80] + "\\n" + FAKE_KEY[80:]
    text = f'{{"output": "ANTHROPIC_API_KEY={wrapped}"}}'

    assert FAKE_KEY not in text  # a literal search sees nothing
    assert secret_spans(text, FAKE_KEY)  # the wrap-tolerant matcher does

    cleaned, replaced = scrub_text(text, [FAKE_KEY])
    assert replaced == 1
    assert "<redacted>" in cleaned
    assert not secret_spans(cleaned, FAKE_KEY)
    assert FAKE_KEY[:40] not in cleaned


def test_scrub_handles_every_wrap_form_and_keeps_json_valid() -> None:
    for separator in ("", "\\n", "\\r\\n", "\n", " ", "\\\\n", "\t"):
        wrapped = FAKE_KEY[:50] + separator + FAKE_KEY[50:]
        document = json.dumps({"trajectory": [{"observation": f"key={wrapped}"}]})
        cleaned, replaced = scrub_text(document, [FAKE_KEY])
        assert replaced == 1, f"missed a key split by {separator!r}"
        assert not secret_spans(cleaned, FAKE_KEY)
        json.loads(cleaned)  # scrubbing must not corrupt the artifact


def test_scrub_replaces_every_occurrence() -> None:
    text = "\n".join([f"line {n} {FAKE_KEY}" for n in range(5)])
    cleaned, replaced = scrub_text(text, [FAKE_KEY])
    assert replaced == 5
    assert not secret_spans(cleaned, FAKE_KEY)


def test_scrub_leaves_unrelated_text_alone() -> None:
    text = "nothing secret here, just sk-ant-api03- and a short token abc123"
    cleaned, replaced = scrub_text(text, [FAKE_KEY])
    assert (cleaned, replaced) == (text, 0)
    # Too short to match safely: matching it would corrupt unrelated bytes.
    assert scrub_text(text, ["abc123"]) == (text, 0)


def test_scrub_tree_cleans_the_artifacts_a_run_actually_publishes(tmp_path: Path) -> None:
    """The end-to-end guard: plant a key in real artifact shapes, then require a clean tree."""
    job = tmp_path / "job" / "crack-7z-hash"
    (job / "agent").mkdir(parents=True)
    (job / "agent" / "trajectory.json").write_text(
        json.dumps({"steps": [{"output": f"ANTHROPIC_API_KEY={FAKE_KEY[:60]}\\n{FAKE_KEY[60:]}"}]})
    )
    (job / "agent" / "terminus_2.pane").write_text(f"$ env | grep API\nANTHROPIC_API_KEY={FAKE_KEY}\n")
    (job / "agent" / "recording.cast").write_text(f'[1.0, "o", "{FAKE_KEY}"]\n')
    (job / "trial.log").write_text(f"starting agent with {FAKE_KEY}\n")
    (job / "result.json").write_text(json.dumps({"task_name": "terminal-bench/crack-7z-hash"}))

    assert len(find_secrets(tmp_path, [FAKE_KEY])) == 4

    files_changed, replacements, touched = scrub_tree(tmp_path, [FAKE_KEY])

    assert files_changed == 4
    assert replacements == 4
    assert sorted(Path(name).name for name in touched) == [
        "recording.cast",
        "terminus_2.pane",
        "trajectory.json",
        "trial.log",
    ]
    # The point of the whole test: nothing left to publish.
    assert find_secrets(tmp_path, [FAKE_KEY]) == []
    json.loads((job / "agent" / "trajectory.json").read_text())
