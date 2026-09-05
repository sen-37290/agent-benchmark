"""Stream Harbor's LLM requests instead of waiting on one silent socket.

WHAT WENT WRONG. Harbor issues every Terminal-Bench model call as a NON-streaming
``litellm.acompletion``: one POST, then a blocking read for the whole response body. Claude
Fable 5.1 at ``reasoning_effort=max`` answers a terminal turn with 19k-49k output tokens, which
takes many minutes -- and for that entire time the socket carries no application bytes at all.
Measured on the stuck run: three of the six connections had ``bytes_received`` frozen at ~3,870
(the TLS handshake and nothing else) for 34 minutes and climbing, while the worker slept in
``epoll_wait``. That is the documented failure mode for this transport -- Anthropic's own
guidance is that long or high-``max_tokens`` requests must stream precisely because a silent
non-streaming socket runs into HTTP timeouts.

A silent socket cannot be diagnosed, and it cannot be distinguished from a dead one: an idle TCP
flow through any NAT or load balancer between here and the API can have its mapping dropped
without a FIN or RST reaching either end, after which the client waits forever on a connection
that no longer exists. Raising the read timeout from 600s to 3600s did not fix that, it only
changed how long each dead request occupied a worker before the retry wrapper started another
one -- up to 8 attempts inside a 4-hour budget. That run was stopped after 108 minutes with 57
agent steps and zero completed tasks; on streaming, the same six tasks reached 47 steps in nine.

WHAT THIS DOES. It flips the transport at LiteLLM's provider seam -- ``litellm.acompletion``, the
same seam ``openai_fallback`` uses -- and hides the change from everything above it:

  * the request goes out with ``stream=True``, so the server emits SSE events (content deltas and
    periodic pings) continuously and the flow is never idle for more than a few seconds;
  * the chunks are consumed here and reassembled with ``litellm.stream_chunk_builder`` into the
    ordinary ``ModelResponse`` Harbor expects, so ``harbor.llms.lite_llm.LiteLLM.call`` -- which
    raises ``NotImplementedError("Streaming is not supported for T bench yet")`` if it is ever
    handed a stream wrapper -- never sees one;
  * the read timeout now means what we always wanted it to mean. On a streaming response httpx
    applies ``read`` per socket read, so it is an inter-BYTE idle timer: it fires seconds after a
    genuine stall and never during a long-but-healthy generation. That is why the idle default is
    minutes rather than the hour a whole-response timeout needed;
  * an absolute wall-clock deadline bounds the complete logical attempt, independent of the idle
    timer, so a stream that dribbles forever still ends.

Both expire as ``litellm.Timeout``, which ``harbor_cost_guard`` already classifies as transient,
so a stalled attempt is retried rather than killing the trial.

OBSERVABILITY. The old logs recorded only successful completions, so an attempt that timed out
inside LiteLLM left no trace and "41 minutes without progress" could not be attributed. Every
attempt now appends one JSON line to ``AGENT_BENCH_LLM_STREAM_LOG`` with latency to the first
chunk, chunk count, elapsed time, token counts and outcome -- enough to tell a slow generation
(chunks arriving) from a provider stall (none, and an error).

``first_chunk_s`` is NOT time to first byte, and the difference matters when reading the log
against ``idle_timeout_s``. Anthropic sends SSE pings while the model thinks; LiteLLM's iterator
does not yield a chunk for them, but they are bytes on the socket and they reset httpx's read
timer. So a turn can legitimately show ``first_chunk_s: 1278`` under a 180s idle timeout -- 21
minutes of thinking, on a connection that was never quiet -- and that is exactly the turn the old
non-streaming transport had no way to survive. A stall looks different: no chunks AND an error.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

#: Set to 0/false to keep the old non-streaming transport.
ENABLED_ENV = "AGENT_BENCH_LLM_STREAMING"
#: Longest gap between two bytes of a streaming response before the attempt is called stalled.
IDLE_ENV = "AGENT_BENCH_LLM_STREAM_IDLE_SECONDS"
#: Absolute ceiling on one logical request, first byte to last.
DEADLINE_ENV = "AGENT_BENCH_LLM_STREAM_DEADLINE_SECONDS"
#: Where to append one JSON line per attempt. Optional.
LOG_ENV = "AGENT_BENCH_LLM_STREAM_LOG"

#: A healthy stream is never quiet this long, because this bounds raw BYTES, not chunks: the
#: server's SSE pings keep the timer fed through a long thinking phase even when no chunk is
#: yielded. Measured on the streaming run, a turn reached its first chunk after 1,278s under a
#: 180s idle timeout and completed normally, which is the proof that pings do arrive. The margin
#: above 180s is for a provider that pings less often; a dead connection still ends in five
#: minutes rather than the 47 the old transport managed.
DEFAULT_IDLE_SECONDS = 300.0
#: Fable's longest observed Terminal-Bench turn (~49k output tokens) lands well inside this.
DEFAULT_DEADLINE_SECONDS = 1800.0
#: Connect/write/pool stay short: a real connection failure must surface immediately, not sit
#: behind the response timeout the way a single-float httpx timeout would make it.
_CONNECT_SECONDS = 30.0
_WRITE_SECONDS = 60.0
_POOL_SECONDS = 60.0

_log_lock = threading.Lock()


def is_enabled() -> bool:
    """True unless the run explicitly asks for the old non-streaming transport."""
    raw = os.environ.get(ENABLED_ENV, "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def configured_idle_seconds() -> float:
    return _positive(IDLE_ENV, DEFAULT_IDLE_SECONDS)


def configured_deadline_seconds() -> float:
    return _positive(DEADLINE_ENV, DEFAULT_DEADLINE_SECONDS)


def _positive(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _record(entry: dict[str, Any]) -> None:
    """Append one line to the per-attempt stream log, if one is configured."""
    path = os.environ.get(LOG_ENV, "").strip()
    if not path:
        return
    entry["at"] = datetime.now(timezone.utc).isoformat()
    line = json.dumps(entry, separators=(",", ":"))
    # One Harbor process runs every trial of the job, so concurrent workers share this file.
    with _log_lock:
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            # Instrumentation must never take down a trial.
            pass


def _correlation(kwargs: dict[str, Any]) -> str | None:
    """A per-trial id for the log, when Harbor supplied one.

    Harbor sets ``extra_body["session_id"]`` per trial when the agent provides one. It is the only
    task-identifying value that reaches this seam; without it the log still orders by time.
    """
    try:
        extra_body = kwargs.get("extra_body") or {}
        value = extra_body.get("session_id")
        return str(value) if value else None
    except Exception:
        return None


def _usage_field(response: Any, name: str) -> int | None:
    try:
        return getattr(response.usage, name, None)
    except Exception:
        return None


def install(idle_seconds: float, deadline_seconds: float) -> bool:
    """Stream every chat completion and hand back an assembled response. True when applied.

    Installed BELOW ``openai_fallback`` (i.e. first), so a fallback rung streams too. The
    Responses-API path Harbor uses for gpt-5.6 goes through ``litellm.aresponses`` and is
    untouched.
    """
    import httpx
    import litellm

    if getattr(litellm, "_agent_bench_streaming", False):
        return True

    original = litellm.acompletion

    # httpx applies `read` per socket read, so on a streaming response this is the inter-byte
    # idle timeout -- the thing the old 3600s whole-response timeout could not express.
    stream_timeout = httpx.Timeout(
        idle_seconds,
        connect=_CONNECT_SECONDS,
        write=_WRITE_SECONDS,
        pool=_POOL_SECONDS,
    )

    async def streaming_acompletion(*args, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("stream"):
            # A caller that already wants a stream gets the raw wrapper; Harbor never does this.
            return await original(*args, **kwargs)

        messages = kwargs.get("messages")
        stream_kwargs = {**kwargs, "stream": True, "timeout": stream_timeout}
        if _supports_stream_options(kwargs.get("model")):
            # What makes OpenAI-shaped providers emit the final usage chunk. Anthropic does not
            # list the parameter (it reports usage in the terminal message_delta regardless), and
            # sending it to a provider that does not take it is only safe while the caller sets
            # `drop_params` -- so ask instead of relying on that.
            stream_kwargs["stream_options"] = {"include_usage": True}

        entry: dict[str, Any] = {
            "session_id": _correlation(kwargs),
            "model": kwargs.get("model"),
            "idle_timeout_s": idle_seconds,
            "deadline_s": deadline_seconds,
        }
        started = time.monotonic()
        first_chunk_at: float | None = None
        chunks: list[Any] = []

        try:
            async with asyncio.timeout(deadline_seconds):
                wrapper = await original(*args, **stream_kwargs)
                async for chunk in wrapper:
                    if first_chunk_at is None:
                        first_chunk_at = time.monotonic()
                    chunks.append(chunk)
        except TimeoutError as error:
            # asyncio.timeout: the wall-clock deadline, not the idle timer. Present it as a
            # litellm.Timeout so the retry layer above treats it as the transient fault it is.
            entry.update(
                {
                    "outcome": "deadline_exceeded",
                    "first_chunk_s": _elapsed(started, first_chunk_at),
                    "chunks": len(chunks),
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
            )
            _record(entry)
            raise litellm.Timeout(
                message=(
                    f"streaming request exceeded the {deadline_seconds:.0f}s wall-clock deadline "
                    f"after {len(chunks)} chunks"
                ),
                model=str(kwargs.get("model")),
                llm_provider=None,
            ) from error
        except Exception as error:
            entry.update(
                {
                    "outcome": "error",
                    "error_type": type(error).__name__,
                    "error": str(error)[:300],
                    "first_chunk_s": _elapsed(started, first_chunk_at),
                    "chunks": len(chunks),
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
            )
            _record(entry)
            raise

        response = litellm.stream_chunk_builder(chunks, messages=messages)
        if response is None:
            # An empty stream is not a valid answer; make it transient so the attempt is retried
            # rather than surfacing to Terminus 2 as an unparseable empty turn.
            entry.update(
                {
                    "outcome": "empty_stream",
                    "chunks": len(chunks),
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
            )
            _record(entry)
            raise litellm.Timeout(
                message="streaming request returned no chunks",
                model=str(kwargs.get("model")),
                llm_provider=None,
            )

        _finalise(response, kwargs.get("model"))
        entry.update(
            {
                "outcome": "ok",
                "served_model": getattr(response, "model", None),
                "finish_reason": _finish_reason(response),
                "first_chunk_s": _elapsed(started, first_chunk_at),
                "chunks": len(chunks),
                "elapsed_s": round(time.monotonic() - started, 3),
                "prompt_tokens": _usage_field(response, "prompt_tokens"),
                "completion_tokens": _usage_field(response, "completion_tokens"),
            }
        )
        _record(entry)
        return response

    litellm.acompletion = streaming_acompletion  # type: ignore[assignment]
    litellm._agent_bench_streaming = True  # type: ignore[attr-defined]
    return True


def _supports_stream_options(model: str | None) -> bool:
    """True when the provider takes ``stream_options``; usage would otherwise be missing."""
    if not model:
        return False
    try:
        import litellm
        from litellm.litellm_core_utils.get_supported_openai_params import (
            get_supported_openai_params,
        )

        _, provider, _, _ = litellm.get_llm_provider(model=model)
        supported = get_supported_openai_params(model=model, custom_llm_provider=provider) or []
        return "stream_options" in supported
    except Exception:
        return False


def _elapsed(started: float, mark: float | None) -> float | None:
    return None if mark is None else round(mark - started, 3)


def _finish_reason(response: Any) -> str | None:
    try:
        return response["choices"][0].get("finish_reason")
    except Exception:
        return None


def _finalise(response: Any, requested_model: str | None) -> None:
    """Give the assembled response the model name and price a non-streamed one arrives with.

    ``stream_chunk_builder`` reassembles content and usage but leaves ``model`` as ``None`` when
    the chunks do not carry one -- and an unnamed response cannot be priced: ``completion_cost``
    raises ``ValueError: Model is None``. Harbor swallows that (``except Exception: cost = 0.0``),
    so the whole run would have reported $0.00, the per-task cost guard would have had nothing to
    count, and ``model_name`` on every trajectory turn would have been null. None of that raises,
    which is exactly why it is worth setting explicitly here.
    """
    import litellm

    try:
        if not getattr(response, "model", None) and requested_model:
            # Match the non-streaming shape: litellm reports the bare model, not "provider/model".
            response.model = requested_model.split("/")[-1]
    except Exception:
        pass

    try:
        hidden = getattr(response, "_hidden_params", None)
        if not isinstance(hidden, dict) or hidden.get("response_cost"):
            return
        hidden["response_cost"] = litellm.completion_cost(
            completion_response=response, model=requested_model or None
        )
    except Exception:
        # Leave it unset: Harbor recomputes from usage when response_cost is absent or zero.
        pass
