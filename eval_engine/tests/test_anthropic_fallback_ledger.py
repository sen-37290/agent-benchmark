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
