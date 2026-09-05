"""Launch Harbor with a per-task cost limit installed.

Harbor's ``Terminus2`` accepts ``max_turns`` but no dollar limit, and Harbor runs every trial of a
job inside one asyncio process. A process-wide counter would therefore conflate concurrent tasks.

``harbor.llms.chat.Chat`` is the right seam: Terminus 2 constructs exactly one ``Chat`` per trial
(``self._chat = Chat(self._llm, ...)``) and that object already accumulates ``total_cost`` from each
response's usage. Wrapping ``Chat.chat`` gives per-trial attribution for free.

It also widens Harbor's LLM retry policy. Harbor decorates ``LiteLLM.call`` with
``stop_after_attempt(3)`` and ``wait_exponential(min=4, max=15)`` -- roughly 30 seconds of
patience, which a provider rate limit or billing outage outlasts easily. When that runs out the
exception propagates and kills the whole trial, discarding an hour of work over one bad request.
``install_llm_retries`` wraps the call in a much longer transient-only retry, so a task survives an
API blip instead of being recorded as a model failure.

Run this module in place of the ``harbor`` console script; every argument is forwarded unchanged:

    uv run python -m agent_benchmark.harnesses.harbor_cost_guard run -d ... -a terminus-2 ...
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time

# Running a file by path puts its own directory on sys.path[0]. This package has a sibling module
# named harbor.py (the harness), which would then shadow the real `harbor` package and make
# `from harbor.llms.chat import Chat` fail with "'harbor' is not a package". Drop that entry: the
# engine itself is imported from the installed project, not from here.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [entry for entry in sys.path if os.path.abspath(entry or os.getcwd()) != _HERE]

from agent_benchmark.harnesses import anthropic_fallback as _anthropic_fallback  # noqa: E402
from agent_benchmark.harnesses import openai_fallback as _openai_fallback  # noqa: E402
from agent_benchmark.harnesses import streaming_completion as _streaming  # noqa: E402
from agent_benchmark.run.costguard import CostLimitExceeded, configured_limit  # noqa: E402
from agent_benchmark.run.retry import is_transient  # noqa: E402

#: How many times one LLM request may be attempted before the trial is allowed to fail.
ATTEMPTS_ENV = "AGENT_BENCH_LLM_RETRY_ATTEMPTS"
#: Ceiling on the total time spent retrying a single request, in seconds.
BUDGET_ENV = "AGENT_BENCH_LLM_RETRY_SECONDS"
#: Read timeout for one NON-streaming LLM request. Only consulted when streaming is turned off:
#: a whole-response timeout cannot tell a long generation from a dead socket, which is why the
#: streaming path replaces it with an inter-byte idle timer plus a wall-clock deadline. See
#: streaming_completion.
REQUEST_TIMEOUT_ENV = "AGENT_BENCH_LLM_REQUEST_TIMEOUT_SECONDS"
DEFAULT_ATTEMPTS = 8
#: Ceiling on the retry sequence for ONE logical model turn. Bounded rather than generous: with
#: streaming, a stalled attempt now ends in minutes, so a turn that is still failing after two
#: hours is broken, not unlucky.
DEFAULT_BUDGET_SECONDS = 7200.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 3600.0
_BASE_DELAY = 10.0
_MAX_DELAY = 300.0


def _positive(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _retry_delay(attempt: int) -> float:
    """Exponential backoff with jitter, so 12 concurrent workers do not retry in lockstep."""
    delay = min(_MAX_DELAY, _BASE_DELAY * (2 ** max(0, attempt - 1)))
    return delay * random.uniform(0.8, 1.2)


def _retryable(error: BaseException) -> bool:
    """Only transient provider faults are retried; a cost stop or a bug must propagate at once."""
    if isinstance(error, CostLimitExceeded):
        return False
    return is_transient(type(error).__name__, str(error))


def install_llm_retries(
    attempts: int,
    budget_seconds: float,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> bool:
    """Wrap ``LiteLLM.call`` in a long transient-only retry. Returns True when applied."""
    if attempts <= 1:
        return False
    from harbor.llms.lite_llm import LiteLLM

    if getattr(LiteLLM, "_agent_bench_llm_retries", False):
        return True

    original = LiteLLM.call

    # Keep genuine connection failures responsive while allowing a non-streaming response body to
    # take up to an hour. A single float would also turn the TCP connect timeout into one hour.
    # The streaming path sets its own, sharper timeout at the acompletion seam and ignores this
    # one, so it is only built when streaming is off.
    import httpx

    request_timeout = (
        None
        if _streaming.is_enabled()
        else httpx.Timeout(
            request_timeout_seconds,
            connect=min(60.0, request_timeout_seconds),
            write=min(60.0, request_timeout_seconds),
            pool=min(60.0, request_timeout_seconds),
        )
    )

    async def retrying_call(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Harbor forwards unknown call kwargs directly to litellm.acompletion(). Use setdefault so
        # an explicit per-call timeout remains authoritative.
        if request_timeout is not None:
            kwargs.setdefault("timeout", request_timeout)
        deadline = time.monotonic() + budget_seconds
        for attempt in range(1, attempts + 1):
            try:
                return await original(self, *args, **kwargs)
            except CostLimitExceeded:
                raise
            except Exception as error:
                delay = _retry_delay(attempt)
                if (
                    attempt == attempts
                    or not _retryable(error)
                    or time.monotonic() + delay > deadline
                ):
                    raise
                print(
                    f"[llm-retry] attempt {attempt}/{attempts} failed with "
                    f"{type(error).__name__}: {str(error)[:200]} -- retrying in {delay:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
                await asyncio.sleep(delay)
        # Unreachable: the final attempt either returns or re-raises above.
        raise AssertionError("retry loop exited without a result")

    LiteLLM.call = retrying_call  # type: ignore[method-assign]
    LiteLLM._agent_bench_llm_retries = True  # type: ignore[attr-defined]
    return True


def install(limit_usd: float) -> bool:
    """Patch ``Chat.chat`` to stop a trial at ``limit_usd``. Returns True when applied."""
    if limit_usd <= 0:
        return False
    from harbor.llms.chat import Chat

    if getattr(Chat, "_agent_bench_cost_guard", False):
        return True

    original = Chat.chat

    async def guarded_chat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Check before the call as well as after: a trial that is already over budget must not be
        # allowed to issue one more request just because the previous response tipped it over.
        spent_before = float(getattr(self, "_cumulative_cost", 0.0) or 0.0)
        if spent_before >= limit_usd:
            raise CostLimitExceeded(spent_before, limit_usd)
        response = await original(self, *args, **kwargs)
        spent = float(getattr(self, "_cumulative_cost", 0.0) or 0.0)
        if spent >= limit_usd:
            raise CostLimitExceeded(spent, limit_usd)
        return response

    Chat.chat = guarded_chat  # type: ignore[method-assign]
    Chat._agent_bench_cost_guard = True  # type: ignore[attr-defined]
    return True


def main() -> None:
    limit = configured_limit()
    if install(limit):
        print(f"[cost-guard] per-task limit ${limit:.2f} installed", file=sys.stderr, flush=True)
    else:
        # Loud, because an unguarded Terminal-Bench run has already produced a $5,019 task.
        print(
            "[cost-guard] WARNING: DISABLED -- no per-task limit set; spend is unbounded",
            file=sys.stderr,
            flush=True,
        )

    # Streaming goes in FIRST so it sits closest to the wire: openai_fallback wraps
    # litellm.acompletion too, and a fallback rung must stream like any other attempt.
    #
    # This is the fix for the stall that left a six-task Terminal-Bench run with zero completions
    # after 108 minutes. Non-streaming, a Fable max-effort turn leaves the socket silent for the
    # whole generation, which is indistinguishable from a connection that has been dropped
    # somewhere in between -- and unrecoverable, because nothing arrives to time out on until the
    # read timeout expires. Streaming keeps bytes flowing, so a stall is detected in minutes.
    idle_seconds = _streaming.configured_idle_seconds()
    deadline_seconds = _streaming.configured_deadline_seconds()
    streaming = _streaming.is_enabled() and _streaming.install(idle_seconds, deadline_seconds)
    if streaming:
        print(
            f"[llm-stream] streaming transport enabled: {idle_seconds:.0f}s inter-byte idle "
            f"timeout, {deadline_seconds:.0f}s wall-clock deadline per attempt",
            file=sys.stderr,
            flush=True,
        )
    else:
        # Loud, because this is the configuration that produced hour-long silent requests.
        print(
            "[llm-stream] WARNING: DISABLED -- requests are non-streaming; a long generation "
            "cannot be told apart from a dead connection",
            file=sys.stderr,
            flush=True,
        )

    attempts = int(_positive(ATTEMPTS_ENV, DEFAULT_ATTEMPTS))
    budget = _positive(BUDGET_ENV, DEFAULT_BUDGET_SECONDS)
    request_timeout = _positive(REQUEST_TIMEOUT_ENV, DEFAULT_REQUEST_TIMEOUT_SECONDS)
    if install_llm_retries(attempts, budget, request_timeout):
        bound = (
            f"{deadline_seconds:.0f}s deadline per attempt"
            if streaming
            else f"{request_timeout:.0f}s response timeout"
        )
        print(
            f"[llm-retry] up to {attempts} attempts per request "
            f"within {budget:.0f}s, {bound}, transient errors only",
            file=sys.stderr,
            flush=True,
        )
    else:
        print("[llm-retry] disabled", file=sys.stderr, flush=True)

    # Server-side fallback, when the run asks for it. Claude Fable 5.1 refuses some
    # Terminal-Bench tasks outright, and a refusal is an HTTP 200 with empty content that
    # Terminus 2 loops on; letting the API retry on another model is what makes those tasks
    # runnable at all. See anthropic_fallback for why this cannot go through LiteLLM's
    # public kwargs.
    fallbacks = _anthropic_fallback.configured_fallbacks()
    if fallbacks is not None:
        _anthropic_fallback.install(fallbacks)
        print(
            f"[fallback] server-side fallback enabled: {json.dumps(fallbacks)}",
            file=sys.stderr,
            flush=True,
        )

    # Client-side model fallback for OpenAI content-policy refusals. gpt-5.6 declines some
    # Terminal-Bench tasks with an HTTP 400 (cyber_policy) that no retry can clear; re-issuing
    # the identical request on the next model in the ladder is what makes those tasks runnable.
    # OpenAI has no server-side equivalent, so this is done at the litellm.acompletion seam.
    # See openai_fallback.
    openai_fallbacks = _openai_fallback.configured_fallbacks()
    if openai_fallbacks is not None:
        _openai_fallback.install(openai_fallbacks)
        print(
            f"[openai-fallback] client-side model fallback enabled: "
            f"{json.dumps(openai_fallbacks)}",
            file=sys.stderr,
            flush=True,
        )

    from harbor.cli.main import app

    # Harbor's Typer app reads sys.argv; present ourselves as the console script it expects.
    sys.argv = ["harbor", *sys.argv[1:]]
    app()


if __name__ == "__main__":
    # Keep tracebacks from the guard readable in Harbor's trial logs.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
