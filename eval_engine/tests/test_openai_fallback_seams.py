"""The refusal ladder has to cover whichever LiteLLM entry point Harbor actually calls.

gpt-5.6 refuses a couple of Terminal-Bench tasks with an HTTP 400 `cyber_policy`, and the
ladder re-issues those calls on the next snapshot. Which function it has to intercept is a
configuration choice, not a constant: Terminus 2 calls ``litellm.acompletion`` normally and
``litellm.aresponses`` whenever ``use_responses_api`` is set -- which is the only way to reach
the top reasoning effort. A ladder installed on the unused seam is worse than no ladder: it
reports as installed, and the refused tasks quietly start dying again.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_benchmark.harnesses import openai_fallback

LADDER = ["openai/gpt-5.6-sol", "openai/gpt-5.6-terra", "openai/gpt-5.6-luna"]
REFUSAL = (
    "litellm.BadRequestError: Error code: 400 - {'error': {'code': 'cyber_policy', "
    "'message': 'This content was flagged for possible cybersecurity risk'}}"
)


def _provider(refuse_models: list[str]):
    """A fake provider call that refuses the named models and serves everything else."""

    async def call(**kwargs):
        model = kwargs["model"]
        if model in refuse_models:
            raise RuntimeError(REFUSAL)
        return {"model": model}

    return call


@pytest.fixture
def litellm_module(monkeypatch, tmp_path):
    """A litellm with both entry points stubbed and the install flag reset."""
    import litellm

    monkeypatch.setattr(litellm, "_agent_bench_openai_fallback", False, raising=False)
    monkeypatch.setattr(litellm, "acompletion", _provider([]), raising=False)
    monkeypatch.setattr(litellm, "aresponses", _provider([]), raising=False)
    monkeypatch.setenv(openai_fallback.LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    return litellm


def _ledger(tmp_path) -> list[dict]:
    path = tmp_path / "ledger.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_ladder_climbs_on_the_responses_seam(litellm_module, tmp_path) -> None:
    """A refusal on /v1/responses is retried down the ladder, exactly as on chat completions."""
    litellm_module.aresponses = _provider(["openai/gpt-5.6-sol"])
    openai_fallback.install(LADDER)

    response = asyncio.run(litellm_module.aresponses(model="openai/gpt-5.6-sol", input="x"))

    assert response == {"model": "openai/gpt-5.6-terra"}
    entries = _ledger(tmp_path)
    assert [entry["outcome"] for entry in entries] == ["refused", "served"]
    assert all(entry["api"] == "responses" for entry in entries)
    assert entries[-1]["served_by_fallback"] is True


def test_one_install_wraps_both_seams(litellm_module) -> None:
    """The ladder must not depend on which transport the profile happens to select."""
    before_acompletion = litellm_module.acompletion
    before_aresponses = litellm_module.aresponses

    openai_fallback.install(LADDER)

    assert litellm_module.acompletion is not before_acompletion
    assert litellm_module.aresponses is not before_aresponses


def test_chat_completions_still_climbs_and_says_so(litellm_module, tmp_path) -> None:
    """The pre-existing seam keeps working, and the ledger names which one served."""
    litellm_module.acompletion = _provider(["openai/gpt-5.6-sol"])
    openai_fallback.install(LADDER)

    response = asyncio.run(litellm_module.acompletion(model="openai/gpt-5.6-sol", messages=[]))

    assert response == {"model": "openai/gpt-5.6-terra"}
    assert [entry["api"] for entry in _ledger(tmp_path)] == [
        "chat_completions",
        "chat_completions",
    ]


def test_a_non_refusal_is_raised_at_once(litellm_module, tmp_path) -> None:
    """Only content-policy refusals climb; a real error reaches the layers above unchanged."""

    async def failing(**kwargs):
        raise RuntimeError("Error code: 400 - context_length_exceeded")

    litellm_module.aresponses = failing
    openai_fallback.install(LADDER)

    with pytest.raises(RuntimeError, match="context_length_exceeded"):
        asyncio.run(litellm_module.aresponses(model="openai/gpt-5.6-sol", input="x"))
    assert _ledger(tmp_path) == []


def test_every_rung_refused_reraises_the_last_refusal(litellm_module, tmp_path) -> None:
    litellm_module.aresponses = _provider(LADDER)
    openai_fallback.install(LADDER)

    with pytest.raises(RuntimeError, match="cyber_policy"):
        asyncio.run(litellm_module.aresponses(model="openai/gpt-5.6-sol", input="x"))

    assert [entry["outcome"] for entry in _ledger(tmp_path)] == [
        "refused",
        "refused",
        "refused",
        "provider_refusal",
    ]
