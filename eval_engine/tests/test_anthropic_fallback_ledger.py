"""The refusal/fallback ledger has to survive the switch to a streaming transport.

A refusal is an HTTP 200 and a fallback-served answer looks like any other answer, so the ledger
is the only place either is recorded. Streaming bypasses ``transform_parsed_response`` entirely,
which would have made the ledger silently empty for every streamed run.
"""

from __future__ import annotations

import json

from agent_benchmark.harnesses import anthropic_fallback


class _Iterator:
    """Stands in for litellm's per-response ModelResponseIterator."""


def _replay(events: list[dict]) -> None:
    iterator = _Iterator()
    for event in events:
        anthropic_fallback._observe_stream_event(iterator, event)


def test_streamed_fallback_is_recorded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(anthropic_fallback.LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    _replay(
        [
            {
                "type": "message_start",
                "message": {"model": "claude-fable-5-1", "usage": {"input_tokens": 10}},
            },
            {"type": "content_block_start", "content_block": {"type": "fallback"}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {
                    "output_tokens": 20,
                    "iterations": [
                        {"type": "refusal_message", "model": "claude-fable-5-1"},
                        {"type": "fallback_message", "model": "claude-opus-4-8"},
                    ],
                },
            },
            {"type": "message_stop"},
        ]
    )

    entry = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert entry["transport"] == "stream"
    assert entry["served_by_fallback"] is True
    assert entry["stop_reason"] == "end_turn"
    assert [i["model"] for i in entry["iterations"]] == ["claude-fable-5-1", "claude-opus-4-8"]


def test_streamed_refusal_is_not_counted_as_served(monkeypatch, tmp_path) -> None:
    """Every rung refusing is the outcome the run most needs to see, and it is not a success."""
    monkeypatch.setenv(anthropic_fallback.LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    _replay(
        [
            {"type": "message_start", "message": {"model": "claude-fable-5-1"}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "refusal", "stop_details": {"category": "cyber"}},
                "usage": {"iterations": [{"type": "fallback_message", "model": "claude-opus-4-8"}]},
            },
            {"type": "message_stop"},
        ]
    )

    entry = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert entry["served_by_fallback"] is False
    assert entry["refusal_category"] == "cyber"


def test_ordinary_streamed_response_records_no_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(anthropic_fallback.LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    _replay(
        [
            {"type": "message_start", "message": {"model": "claude-fable-5-1"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
            {"type": "message_stop"},
        ]
    )

    entry = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert entry["served_by_fallback"] is False
    assert entry["served_model"] == "claude-fable-5-1"
    assert entry["iterations"] == []


def test_state_does_not_leak_between_responses(monkeypatch, tmp_path) -> None:
    """One iterator is reused per response; a stale fallback flag would overcount."""
    monkeypatch.setenv(anthropic_fallback.LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    iterator = _Iterator()
    first = [
        {"type": "message_start", "message": {"model": "claude-fable-5-1"}},
        {"type": "content_block_start", "content_block": {"type": "fallback"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
        {"type": "message_stop"},
    ]
    second = [
        {"type": "message_start", "message": {"model": "claude-fable-5-1"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
        {"type": "message_stop"},
    ]
    for event in first + second:
        anthropic_fallback._observe_stream_event(iterator, event)

    entries = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert [e["served_by_fallback"] for e in entries] == [True, False]


def test_ledger_is_written_through_the_real_streaming_path(monkeypatch, tmp_path) -> None:
    """Drive litellm's actual Anthropic stream handler, not just the event observer.

    The unit tests above replay events straight into ``_observe_stream_event``, which is why they
    all passed while the first streaming run produced an EMPTY ledger: LiteLLM finishes a response
    at ``message_delta`` and never dispatches ``message_stop`` to ``chunk_parser``, so the hook sat
    on an event that never arrives. Only a test that goes through ``litellm.acompletion`` catches
    that, so this one mocks the HTTP transport and asserts a line comes out the other end.
    """
    import asyncio
    import json as _json

    import httpx
    import litellm
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

    from agent_benchmark.harnesses import streaming_completion

    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv(anthropic_fallback.LEDGER_ENV, str(ledger))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-fable-5-1",
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hello"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    body = "".join(f"event: {e['type']}\ndata: {_json.dumps(e)}\n\n" for e in events)

    client = AsyncHTTPHandler(concurrent_limit=1)
    client.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=body.encode(), headers={"content-type": "text/event-stream"}
            )
        )
    )

    monkeypatch.setattr(litellm, "_agent_bench_streaming", False, raising=False)
    original_acompletion = litellm.acompletion
    try:
        streaming_completion.install(30.0, 60.0)
        anthropic_fallback.install("default")
        response = asyncio.run(
            litellm.acompletion(
                model="anthropic/claude-fable-5-1",
                messages=[{"role": "user", "content": "hi"}],
                client=client,
                drop_params=True,
            )
        )
    finally:
        litellm.acompletion = original_acompletion
        litellm._agent_bench_streaming = False

    assert response["choices"][0]["message"]["content"] == "hello"
    entry = json.loads(ledger.read_text().splitlines()[0])
    assert entry["served_model"] == "claude-fable-5-1"
    assert entry["stop_reason"] == "end_turn"
    assert entry["served_by_fallback"] is False
