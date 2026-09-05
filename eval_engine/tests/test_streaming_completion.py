"""The streaming transport that replaced Harbor's silent non-streaming requests.

The failure being guarded against is not an exception: it is a socket that receives nothing for
an hour while the worker waits. So these tests assert the two properties that make that
impossible -- a stalled stream ends as a retryable timeout, and a healthy one is handed back as
the ordinary response Harbor's non-streaming code path expects.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import litellm
import pytest

from agent_benchmark.harnesses import streaming_completion


def _chunk(content: str = "", finish_reason: str | None = None, usage: dict | None = None):
    """One OpenAI-shaped stream chunk, the form litellm hands to stream_chunk_builder."""
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "claude-fable-5-1",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return litellm.ModelResponseStream(**payload)


@pytest.fixture
def uninstalled(monkeypatch):
    """Each test installs onto a pristine litellm.acompletion."""
    monkeypatch.setattr(litellm, "acompletion", litellm.acompletion, raising=False)
    monkeypatch.setattr(litellm, "_agent_bench_streaming", False, raising=False)
    yield
    litellm._agent_bench_streaming = False


def _install_with(monkeypatch, uninstalled, stream_factory, idle=5.0, deadline=5.0):
    """Install the wrapper over a fake acompletion returning ``stream_factory``'s chunks."""
    seen: dict = {}

    async def fake_acompletion(*args, **kwargs):
        seen.update(kwargs)
        return stream_factory()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    assert streaming_completion.install(idle, deadline)
    return seen


def test_stream_is_reassembled_into_a_plain_response(monkeypatch, uninstalled) -> None:
    """Harbor raises NotImplementedError on a stream wrapper, so it must never see one."""

    def chunks():
        async def gen():
            yield _chunk("hello ")
            yield _chunk("world", finish_reason="stop")

        return gen()

    seen = _install_with(monkeypatch, uninstalled, chunks)
    response = asyncio.run(
        litellm.acompletion(
            model="anthropic/claude-fable-5-1", messages=[{"role": "user", "content": "hi"}]
        )
    )

    assert not isinstance(response, type(chunks()))
    assert response["choices"][0]["message"]["content"] == "hello world"
    assert response["choices"][0]["finish_reason"] == "stop"
    # The request itself went out as a stream.
    assert seen["stream"] is True


def test_read_timeout_is_the_inter_byte_idle_timer(monkeypatch, uninstalled) -> None:
    """The whole point of streaming: `read` now bounds silence, not the whole response."""

    def chunks():
        async def gen():
            yield _chunk("x", finish_reason="stop")

        return gen()

    seen = _install_with(monkeypatch, uninstalled, chunks, idle=123.0)
    asyncio.run(litellm.acompletion(model="anthropic/claude-fable-5-1", messages=[]))

    timeout = seen["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 123.0
    # A connection that cannot be made must still fail fast rather than wait out the idle timer.
    assert timeout.connect == 30.0


def test_wall_clock_deadline_raises_a_retryable_timeout(monkeypatch, uninstalled) -> None:
    """A stream that dribbles forever ends as a transient fault, not an occupied worker."""

    def chunks():
        async def gen():
            while True:
                await asyncio.sleep(0.01)
                yield _chunk("x")

        return gen()

    _install_with(monkeypatch, uninstalled, chunks, deadline=0.2)

    with pytest.raises(litellm.Timeout):
        asyncio.run(litellm.acompletion(model="anthropic/claude-fable-5-1", messages=[]))


def test_empty_stream_is_retryable_not_an_empty_turn(monkeypatch, uninstalled) -> None:
    """An empty answer would loop Terminus 2; a timeout is retried by harbor_cost_guard."""

    def chunks():
        async def gen():
            return
            yield  # pragma: no cover -- makes this an async generator

        return gen()

    _install_with(monkeypatch, uninstalled, chunks)

    with pytest.raises(litellm.Timeout):
        asyncio.run(litellm.acompletion(model="anthropic/claude-fable-5-1", messages=[]))


def test_attempt_log_records_first_byte_and_outcome(monkeypatch, uninstalled, tmp_path) -> None:
    """The observability gap: a timed-out attempt used to leave no trace at all."""

    def chunks():
        async def gen():
            yield _chunk("hi", finish_reason="stop")

        return gen()

    log = tmp_path / "stream.jsonl"
    monkeypatch.setenv(streaming_completion.LOG_ENV, str(log))
    _install_with(monkeypatch, uninstalled, chunks)
    asyncio.run(litellm.acompletion(model="anthropic/claude-fable-5-1", messages=[]))

    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["outcome"] == "ok"
    assert entry["chunks"] == 1
    assert entry["first_byte_s"] is not None
    assert entry["finish_reason"] == "stop"


def test_timed_out_attempt_is_logged(monkeypatch, uninstalled, tmp_path) -> None:
    def chunks():
        async def gen():
            while True:
                await asyncio.sleep(0.01)
                yield _chunk("x")

        return gen()

    log = tmp_path / "stream.jsonl"
    monkeypatch.setenv(streaming_completion.LOG_ENV, str(log))
    _install_with(monkeypatch, uninstalled, chunks, deadline=0.2)
    with pytest.raises(litellm.Timeout):
        asyncio.run(litellm.acompletion(model="anthropic/claude-fable-5-1", messages=[]))

    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["outcome"] == "deadline_exceeded"
    assert entry["chunks"] > 0


def test_caller_requested_stream_is_passed_through(monkeypatch, uninstalled) -> None:
    """Nothing in Harbor does this, but the wrapper must not consume someone else's stream."""
    sentinel = object()

    async def fake_acompletion(*args, **kwargs):
        return sentinel

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    assert streaming_completion.install(5.0, 5.0)

    assert asyncio.run(litellm.acompletion(model="m", messages=[], stream=True)) is sentinel


def test_disabling_streaming_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv(streaming_completion.ENABLED_ENV, "0")
    assert not streaming_completion.is_enabled()
    monkeypatch.setenv(streaming_completion.ENABLED_ENV, "")
    assert streaming_completion.is_enabled()


def test_assembled_response_is_named_and_priced(monkeypatch, uninstalled) -> None:
    """A streamed response arrives unnamed, and an unnamed response silently costs $0.00.

    litellm's stream_chunk_builder leaves ``model`` as None when the chunks carry none, and
    ``completion_cost`` then raises ValueError -- which Harbor catches and turns into 0.0. The
    per-task cost guard counts that as free, so the guard would never fire and the run would
    report no spend at all.
    """

    def chunks():
        async def gen():
            # No model on the chunk: this is what litellm's Anthropic stream iterator emits.
            chunk = _chunk(
                "hi", finish_reason="stop", usage={"prompt_tokens": 1000, "completion_tokens": 200}
            )
            chunk.model = None
            yield chunk

        return gen()

    _install_with(monkeypatch, uninstalled, chunks)
    response = asyncio.run(
        litellm.acompletion(
            model="anthropic/claude-fable-5-1", messages=[{"role": "user", "content": "hi"}]
        )
    )

    assert response.model == "claude-fable-5-1"
    assert response._hidden_params["response_cost"] > 0
