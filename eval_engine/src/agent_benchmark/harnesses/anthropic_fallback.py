"""Enable Anthropic server-side fallback for Harbor's LiteLLM calls.

Claude Fable 5.1 answers a refused request with HTTP 200, ``stop_reason: "refusal"``
and empty content. LiteLLM surfaces that as ``finish_reason: "content_filter"`` with
an empty message, which Terminus 2 cannot parse -- so the agent re-prompts, is refused
again, and burns the task's budget on a loop it can never exit. That is how 13 of the
24 Terminal-Bench tasks in this re-run were lost.

Server-side fallback fixes it at the source: the API retries the refused request on
another model inside the same call and returns that model's answer. It is requested
with a ``fallbacks`` body parameter plus the ``server-side-fallback-2026-07-01`` beta
header -- and neither can be passed through LiteLLM's public surface:

  * ``extra_body`` is not unpacked for the Anthropic provider. LiteLLM forwards the
    literal key, and the API rejects it: "extra_body: Extra inputs are not permitted".
  * A top-level ``fallbacks=`` kwarg collides with LiteLLM's OWN router-level fallback
    parameter, which expects a list of model names and raises
    ``TypeError: can only concatenate list (not "str") to list``.
  * ``extra_headers`` does carry the beta header as far as ``validate_environment``, but
    LiteLLM then filters ``anthropic-beta`` against an allowlist it ships in
    ``anthropic_beta_headers_manager`` and DROPS any value it does not recognise -- silently,
    at debug log level. The header never reaches the wire, and the API rejects the body
    parameter it was supposed to authorise: "fallbacks: Extra inputs are not permitted".

So the parameter is injected at LiteLLM's provider seam instead, the same way
``harbor_cost_guard`` installs the per-task limit: by wrapping the two ``AnthropicConfig``
methods that build the outgoing request. Both are pure transforms with stable
signatures, and the wrappers only add fields.

Prompt caching is unaffected and needs nothing here: Harbor already tags the four most
recent content blocks with ``cache_control`` in ``harbor.llms.utils.add_anthropic_caching``,
and server-side fallback applies fallback credit itself, so the retry is billed as
though the conversation had been on the fallback model all along.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

#: Fallback routing for the run: ``default`` for Anthropic's per-category recommendation,
#: or a JSON list of up to three entries, e.g. ``[{"model": "claude-opus-4-8"}]``.
FALLBACKS_ENV = "AGENT_BENCH_ANTHROPIC_FALLBACKS"
#: Where to append one JSON line per Anthropic response. Optional.
LEDGER_ENV = "AGENT_BENCH_FALLBACK_LEDGER"

BETA_HEADER_VALUE = "server-side-fallback-2026-07-01"

_ledger_lock = threading.Lock()


def configured_fallbacks() -> str | list[dict[str, Any]] | None:
    """Parse the requested routing, or None when the run does not use fallback."""
    raw = os.environ.get(FALLBACKS_ENV, "").strip()
    if not raw:
        return None
    if raw == "default":
        return "default"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{FALLBACKS_ENV} must be 'default' or a JSON list: {error}") from error
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"{FALLBACKS_ENV} must be 'default' or a non-empty JSON list")
    for entry in parsed:
        if not isinstance(entry, dict) or "model" not in entry:
            raise ValueError(f"{FALLBACKS_ENV} entries must be objects with a 'model' key")
    if len(parsed) > 3:
        raise ValueError(f"{FALLBACKS_ENV} accepts at most 3 fallback models")
    return parsed


def _record(entry: dict[str, Any]) -> None:
    """Append one line to the refusal/fallback ledger, if one is configured.

    A refusal is an HTTP 200 and a fallback-served answer is indistinguishable from a
    normal one downstream, so neither shows up in error rates or in Harbor's own logs.
    Without this ledger the run cannot report how often the fallback actually fired.
    """
    path = os.environ.get(LEDGER_ENV, "").strip()
    if not path:
        return
    entry["at"] = datetime.now(timezone.utc).isoformat()
    line = json.dumps(entry, separators=(",", ":"))
    # One Harbor process runs every trial of the job, so concurrent workers share this file.
    with _ledger_lock:
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            # Instrumentation must never take down a trial.
            pass


def _register_beta_header() -> None:
    """Add the beta value to LiteLLM's anthropic-beta allowlist.

    LiteLLM ships a mapping of the beta headers it knows about and drops everything else
    before the request is sent, so setting the header is not enough on its own -- the value
    has to be registered here or it is filtered out. The mapping is a module-level dict
    cached by reference, and the value maps to itself because the Claude API takes the
    header verbatim.
    """
    from litellm import anthropic_beta_headers_manager as beta_manager

    config = beta_manager._load_beta_headers_config()
    for provider in ("anthropic",):
        config.setdefault(provider, {})[BETA_HEADER_VALUE] = BETA_HEADER_VALUE


def install(fallbacks: str | list[dict[str, Any]]) -> bool:
    """Send ``fallbacks`` on every Anthropic request. Returns True when applied."""
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig

    if getattr(AnthropicConfig, "_agent_bench_server_side_fallback", False):
        return True

    _register_beta_header()

    original_validate = AnthropicConfig.validate_environment
    original_transform = AnthropicConfig.transform_request
    original_parse = AnthropicConfig.transform_parsed_response

    def validate_environment(self, headers, *args, **kwargs):  # type: ignore[no-untyped-def]
        headers = original_validate(self, headers, *args, **kwargs)
        # Merge rather than assign: LiteLLM sets its own beta values (context management,
        # structured output, fast mode) on this same header.
        self._ensure_beta_header(headers, BETA_HEADER_VALUE)
        return headers

    def transform_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        data = original_transform(self, *args, **kwargs)
        data["fallbacks"] = fallbacks
        return data

    def transform_parsed_response(self, completion_response, *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            usage = completion_response.get("usage") or {}
            iterations = usage.get("iterations") or []
            stop_reason = completion_response.get("stop_reason")
            _record(
                {
                    "served_model": completion_response.get("model"),
                    "stop_reason": stop_reason,
                    "refusal_category": (completion_response.get("stop_details") or {}).get(
                        "category"
                    ),
                    # A fallback_message entry means a fallback model ran; paired with a
                    # non-refusal stop_reason it means the fallback served the answer.
                    "served_by_fallback": (
                        any(i.get("type") == "fallback_message" for i in iterations)
                        and stop_reason != "refusal"
                    ),
                    "iterations": [
                        {
                            "type": i.get("type"),
                            "model": i.get("model"),
                            "input_tokens": i.get("input_tokens"),
                            "output_tokens": i.get("output_tokens"),
                            "cache_read_input_tokens": i.get("cache_read_input_tokens"),
                            "cache_creation_input_tokens": i.get("cache_creation_input_tokens"),
                        }
                        for i in iterations
                    ],
                }
            )
        except Exception:
            # Never let instrumentation break response parsing.
            pass
        return original_parse(self, completion_response, *args, **kwargs)

    AnthropicConfig.validate_environment = validate_environment  # type: ignore[method-assign]
    AnthropicConfig.transform_request = transform_request  # type: ignore[method-assign]
    AnthropicConfig.transform_parsed_response = transform_parsed_response  # type: ignore[method-assign]
    AnthropicConfig._agent_bench_server_side_fallback = True  # type: ignore[attr-defined]
    _install_streaming_ledger()
    return True


def _install_streaming_ledger() -> None:
    """Keep the ledger writing when the run streams (see streaming_completion).

    ``transform_parsed_response`` is the non-streaming parse path only; a streamed response never
    passes through it, so without this the ledger would silently record nothing -- and a refusal
    is an HTTP 200 that shows up nowhere else. ``ModelResponseIterator.chunk_parser`` receives the
    raw Anthropic SSE events, which carry the same fields the parsed body does: the served model
    in ``message_start``, the stop reason and final usage in ``message_delta``, and a ``fallback``
    content block when a fallback model took over.

    ``message_stop`` is NOT one of them, and assuming it was is what left the first streaming run
    with an empty ledger. LiteLLM finishes the response at ``message_delta`` and never dispatches
    the trailing event to the parser -- six events into a mocked stream, five reach
    ``chunk_parser``. So the line is written at ``message_delta``, the last event carrying
    anything, and ``message_stop`` only closes the state out if a version ever does deliver it.

    The request body itself needs nothing extra: ``transform_request`` builds it for the streaming
    call too, so ``fallbacks`` and the beta header are already on the wire.
    """
    from litellm.llms.anthropic.chat.handler import ModelResponseIterator

    if getattr(ModelResponseIterator, "_agent_bench_fallback_ledger", False):
        return

    original_parser = ModelResponseIterator.chunk_parser

    def chunk_parser(self, chunk, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Never let instrumentation break the stream.
        with contextlib.suppress(Exception):
            _observe_stream_event(self, chunk)
        return original_parser(self, chunk, *args, **kwargs)

    ModelResponseIterator.chunk_parser = chunk_parser  # type: ignore[method-assign]
    ModelResponseIterator._agent_bench_fallback_ledger = True  # type: ignore[attr-defined]


def _observe_stream_event(iterator: Any, chunk: Any) -> None:
    """Accumulate one response's fallback facts, and emit them when the response finishes.

    State lives on the iterator instance, and LiteLLM builds one iterator per response, so
    concurrent trials cannot interleave.
    """
    if not isinstance(chunk, dict):
        return
    event = chunk.get("type")
    state = getattr(iterator, "_agent_bench_state", None)
    if state is None or event == "message_start":
        state = {
            "served_model": None,
            "stop_reason": None,
            "refusal_category": None,
            "iterations": [],
            "saw_fallback_block": False,
            "emitted": False,
        }
        iterator._agent_bench_state = state

    if event == "message_start":
        message = chunk.get("message") or {}
        state["served_model"] = message.get("model")
        usage = message.get("usage") or {}
        state["iterations"] = list(usage.get("iterations") or [])
    elif event == "content_block_start":
        if (chunk.get("content_block") or {}).get("type") == "fallback":
            state["saw_fallback_block"] = True
    elif event == "message_delta":
        delta = chunk.get("delta") or {}
        state["stop_reason"] = delta.get("stop_reason") or state["stop_reason"]
        state["refusal_category"] = (delta.get("stop_details") or {}).get("category") or state[
            "refusal_category"
        ]
        usage = chunk.get("usage") or {}
        if usage.get("iterations"):
            state["iterations"] = list(usage["iterations"])
        # The last event LiteLLM dispatches -- write the line here, not at message_stop.
        _emit_state(state)
    elif event == "message_stop":
        _emit_state(state)
        iterator._agent_bench_state = None


def _emit_state(state: dict[str, Any]) -> None:
    """Write one ledger line for a finished response, at most once."""
    if state.get("emitted"):
        return
    state["emitted"] = True
    iterations = state["iterations"]
    served_by_fallback = (
        state["saw_fallback_block"] or any(i.get("type") == "fallback_message" for i in iterations)
    ) and state["stop_reason"] != "refusal"
    _record(
        {
            "transport": "stream",
            "served_model": state["served_model"],
            "stop_reason": state["stop_reason"],
            "refusal_category": state["refusal_category"],
            "served_by_fallback": served_by_fallback,
            "iterations": [
                {
                    "type": i.get("type"),
                    "model": i.get("model"),
                    "input_tokens": i.get("input_tokens"),
                    "output_tokens": i.get("output_tokens"),
                    "cache_read_input_tokens": i.get("cache_read_input_tokens"),
                    "cache_creation_input_tokens": i.get("cache_creation_input_tokens"),
                }
                for i in iterations
            ],
        }
    )
