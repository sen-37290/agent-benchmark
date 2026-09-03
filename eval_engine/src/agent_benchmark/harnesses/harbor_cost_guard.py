"""Launch Harbor with a per-task cost limit installed.

Harbor's ``Terminus2`` accepts ``max_turns`` but no dollar limit, and Harbor runs every trial of a
job inside one asyncio process. A process-wide counter would therefore conflate concurrent tasks.

``harbor.llms.chat.Chat`` is the right seam: Terminus 2 constructs exactly one ``Chat`` per trial
(``self._chat = Chat(self._llm, ...)``) and that object already accumulates ``total_cost`` from each
response's usage. Wrapping ``Chat.chat`` gives per-trial attribution for free.

Run this module in place of the ``harbor`` console script; every argument is forwarded unchanged:

    uv run python -m agent_benchmark.harnesses.harbor_cost_guard run -d ... -a terminus-2 ...
"""

from __future__ import annotations

import os
import sys

from agent_benchmark.run.costguard import CostLimitExceeded, configured_limit


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
        print("[cost-guard] disabled (no per-task limit set)", file=sys.stderr, flush=True)

    from harbor.cli.main import app

    # Harbor's Typer app reads sys.argv; present ourselves as the console script it expects.
    sys.argv = ["harbor", *sys.argv[1:]]
    app()


if __name__ == "__main__":
    # Keep tracebacks from the guard readable in Harbor's trial logs.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
