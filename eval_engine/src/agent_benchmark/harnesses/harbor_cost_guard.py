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

from agent_benchmark.run.costguard import CostLimitExceeded, configured_limit  # noqa: E402
from agent_benchmark.harnesses.anthropic_fallback import (  # noqa: E402
    configured_fallbacks,
    install as install_anthropic_fallback,
)
from agent_benchmark.harnesses.openai_fallback import (  # noqa: E402
    configured_fallbacks as configured_openai_fallbacks,
    install as install_openai_fallback,
)
from agent_benchmark.run.retry import is_transient  # noqa: E402

#: How many times one LLM request may be attempted before the trial is allowed to fail.
ATTEMPTS_ENV = "AGENT_BENCH_LLM_RETRY_ATTEMPTS"
#: Ceiling on the total time spent retrying a single request, in seconds.
BUDGET_ENV = "AGENT_BENCH_LLM_RETRY_SECONDS"
DEFAULT_ATTEMPTS = 8
DEFAULT_BUDGET_SECONDS = 1800.0
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


def install_llm_retries(attempts: int, budget_seconds: float) -> bool:
    """Wrap ``LiteLLM.call`` in a long transient-only retry. Returns True when applied."""
    if attempts <= 1:
        return False
    from harbor.llms.lite_llm import LiteLLM

    if getattr(LiteLLM, "_agent_bench_llm_retries", False):
        return True

    original = LiteLLM.call

    async def retrying_call(self, *args, **kwargs):  # type: ignore[no-untyped-def]
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

    attempts = int(_positive(ATTEMPTS_ENV, DEFAULT_ATTEMPTS))
    budget = _positive(BUDGET_ENV, DEFAULT_BUDGET_SECONDS)
    if install_llm_retries(attempts, budget):
        print(
            f"[llm-retry] up to {attempts} attempts per request "
            f"within {budget:.0f}s, transient errors only",
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
    fallbacks = configured_fallbacks()
    if fallbacks is not None:
        install_anthropic_fallback(fallbacks)
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
    openai_fallbacks = configured_openai_fallbacks()
    if openai_fallbacks is not None:
        install_openai_fallback(openai_fallbacks)
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
