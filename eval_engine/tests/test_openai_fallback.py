from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import litellm
import pytest

from agent_benchmark.harnesses import openai_fallback


class RefusalError(Exception):
    pass


@pytest.fixture
def uninstalled(monkeypatch):
    original_acompletion = litellm.acompletion
    original_aresponses = litellm.aresponses
    monkeypatch.setattr(litellm, "_agent_bench_openai_fallback", False, raising=False)
    yield
    litellm.acompletion = original_acompletion
    litellm.aresponses = original_aresponses


@pytest.mark.parametrize(
    ("api_name", "expected_api"),
    [
        ("acompletion", "chat_completions"),
        ("aresponses", "responses"),
    ],
)
def test_content_policy_refusal_falls_back_on_both_api_paths(
    monkeypatch, tmp_path, uninstalled, api_name, expected_api
):
    calls = []
    response = SimpleNamespace(model="gpt-5.6-terra")

    async def fake_call(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise RefusalError("400 cyber_policy")
        return response

    monkeypatch.setattr(litellm, api_name, fake_call)
    ledger = tmp_path / "openai_fallback.jsonl"
    monkeypatch.setenv(openai_fallback.LEDGER_ENV, str(ledger))
    ladder = [
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-luna",
    ]

    assert openai_fallback.install(ladder)
    result = asyncio.run(
        getattr(litellm, api_name)(
            model="openai/gpt-5.6-sol",
            reasoning={"effort": "max"},
        )
    )

    assert result is response
    assert [call["model"] for call in calls] == ladder[:3]
    assert all(call["reasoning"] == {"effort": "max"} for call in calls)
    entries = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [entry["outcome"] for entry in entries] == [
        "refused",
        "refused",
        "served",
    ]
    assert all(entry["api"] == expected_api for entry in entries)
    assert entries[-1]["served_model"] == "gpt-5.6-terra"
    assert entries[-1]["served_by_fallback"] is True


def test_non_policy_error_does_not_fall_back(monkeypatch, uninstalled):
    calls = []
    error = ValueError("unsupported reasoning effort")

    async def fake_aresponses(*args, **kwargs):
        calls.append(kwargs)
        raise error

    monkeypatch.setattr(litellm, "aresponses", fake_aresponses)
    assert openai_fallback.install(["openai/sol", "openai/terra"])

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(litellm.aresponses(model="openai/sol"))

    assert exc_info.value is error
    assert [call["model"] for call in calls] == ["openai/sol"]


def test_all_refusals_raise_last_error_and_record_terminal_state(
    monkeypatch, tmp_path, uninstalled
):
    errors = [RefusalError("cyber_policy first"), RefusalError("cyber_policy last")]

    async def fake_acompletion(*args, **kwargs):
        raise errors.pop(0)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    ledger = tmp_path / "openai_fallback.jsonl"
    monkeypatch.setenv(openai_fallback.LEDGER_ENV, str(ledger))
    assert openai_fallback.install(["openai/sol", "openai/luna"])

    with pytest.raises(RefusalError, match="last"):
        asyncio.run(litellm.acompletion(model="openai/sol"))

    entries = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [entry["outcome"] for entry in entries] == [
        "refused",
        "refused",
        "provider_refusal",
    ]


def test_install_is_idempotent(monkeypatch, uninstalled):
    async def fake_acompletion(*args, **kwargs):
        return None

    async def fake_aresponses(*args, **kwargs):
        return None

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "aresponses", fake_aresponses)
    ladder = ["openai/sol", "openai/luna"]

    assert openai_fallback.install(ladder)
    wrapped_acompletion = litellm.acompletion
    wrapped_aresponses = litellm.aresponses
    assert openai_fallback.install(ladder)

    assert litellm.acompletion is wrapped_acompletion
    assert litellm.aresponses is wrapped_aresponses
