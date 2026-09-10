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


# --- Streamed refusals ----------------------------------------------------------------------
#
# A streamed refusal does not come out of the await that opens the request. That call succeeds
# and returns an iterator; litellm raises MidStreamFallbackError later, while the iterator is
# being read. The first version of this wrap had already returned by then and never saw it --
# on a real run it logged 1,029 served calls, zero refusals, and both cyber tasks died anyway.

MID_STREAM_REFUSAL = (
    "litellm.MidStreamFallbackError: litellm.APIError: This content was flagged for "
    "possible cybersecurity risk."
)


def _streaming_provider(refuse_models: list[str], preamble: int = 3):
    """A streamed call that emits `preamble` events, then refuses the named models."""

    async def call(**kwargs):
        model = kwargs["model"]

        async def stream():
            for i in range(preamble):
                yield {"type": "response.created", "model": model, "seq": i}
            if model in refuse_models:
                raise RuntimeError(MID_STREAM_REFUSAL)
            yield {"type": "response.completed", "model": model}

        return stream()

    return call


async def _drain(gen):
    return [event async for event in gen]


def test_streamed_refusal_climbs_the_ladder_on_the_responses_seam(litellm_module, tmp_path):
    """The failure this fixes: refusal after several events, on the Responses API."""
    litellm_module.aresponses = _streaming_provider(["openai/gpt-5.6-sol"])
    openai_fallback.install(LADDER)

    async def run():
        gen = await litellm_module.aresponses(model="openai/gpt-5.6-sol", input="x", stream=True)
        return await _drain(gen)

    events = asyncio.run(run())

    # The terminal event -- the only one Harbor keeps -- comes from the fallback model.
    terminal = [e for e in events if e["type"] == "response.completed"]
    assert terminal == [{"type": "response.completed", "model": "openai/gpt-5.6-terra"}]

    entries = _ledger(tmp_path)
    assert [e["outcome"] for e in entries] == ["refused", "served"]
    assert entries[0]["phase"] == "mid_stream"
    assert entries[0]["events_before_refusal"] == 3
    assert entries[0]["reissued"] is True
    assert entries[1]["served_by_fallback"] is True


def test_chat_streaming_does_not_splice_two_answers_together(litellm_module, tmp_path):
    """stream_chunk_builder concatenates every chunk, so a mid-stream restart would corrupt it.

    Raising is the same outcome as having no ladder; a spliced response would be worse than
    either, because nothing downstream could detect it.
    """
    litellm_module.acompletion = _streaming_provider(["openai/gpt-5.6-sol"])
    openai_fallback.install(LADDER)

    async def run():
        gen = await litellm_module.acompletion(model="openai/gpt-5.6-sol", messages=[], stream=True)
        return await _drain(gen)

    with pytest.raises(RuntimeError, match="cybersecurity"):
        asyncio.run(run())

    entry = _ledger(tmp_path)[-1]
    assert entry["phase"] == "mid_stream"
    assert entry["reissued"] is False


def test_chat_streaming_still_climbs_when_nothing_was_yielded(litellm_module, tmp_path):
    """With no chunk delivered yet there is nothing to corrupt, so the ladder applies."""
    litellm_module.acompletion = _streaming_provider(["openai/gpt-5.6-sol"], preamble=0)
    openai_fallback.install(LADDER)

    async def run():
        gen = await litellm_module.acompletion(model="openai/gpt-5.6-sol", messages=[], stream=True)
        return await _drain(gen)

    events = asyncio.run(run())

    assert events == [{"type": "response.completed", "model": "openai/gpt-5.6-terra"}]
    assert _ledger(tmp_path)[0]["reissued"] is True


def test_every_rung_refused_mid_stream_reraises(litellm_module, tmp_path):
    litellm_module.aresponses = _streaming_provider(LADDER)
    openai_fallback.install(LADDER)

    async def run():
        gen = await litellm_module.aresponses(model="openai/gpt-5.6-sol", input="x", stream=True)
        return await _drain(gen)

    with pytest.raises(RuntimeError, match="cybersecurity"):
        asyncio.run(run())

    assert [e["outcome"] for e in _ledger(tmp_path)] == [
        "refused",
        "refused",
        "refused",
        "provider_refusal",
    ]


def test_a_non_refusal_mid_stream_is_raised_untouched(litellm_module, tmp_path):
    """Only content-policy refusals climb; a real stream failure must surface unchanged."""

    async def call(**kwargs):
        async def stream():
            yield {"type": "response.created"}
            raise RuntimeError("connection reset by peer")

        return stream()

    litellm_module.aresponses = call
    openai_fallback.install(LADDER)

    async def run():
        gen = await litellm_module.aresponses(model="openai/gpt-5.6-sol", input="x", stream=True)
        return await _drain(gen)

    with pytest.raises(RuntimeError, match="connection reset"):
        asyncio.run(run())
    assert _ledger(tmp_path) == []


def test_an_unrefused_stream_passes_every_event_through(litellm_module, tmp_path):
    """The wrap must be invisible when nothing is refused."""
    litellm_module.aresponses = _streaming_provider([])
    openai_fallback.install(LADDER)

    async def run():
        gen = await litellm_module.aresponses(model="openai/gpt-5.6-sol", input="x", stream=True)
        return await _drain(gen)

    events = asyncio.run(run())

    assert len(events) == 4
    assert all(e["model"] == "openai/gpt-5.6-sol" for e in events)
    entry = _ledger(tmp_path)[-1]
    assert entry["outcome"] == "served" and entry["served_by_fallback"] is False
