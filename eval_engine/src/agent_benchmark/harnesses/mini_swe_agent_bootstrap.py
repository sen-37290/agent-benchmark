"""Launch the official mini-swe-agent SWE-bench runner with provider fallbacks installed.

WHY THIS EXISTS. `--anthropic-fallbacks` / `--openai-fallbacks` were wired for Harbor only:
`agents/terminus_2.py` puts them in the Harbor process environment and `harbor_cost_guard.main()`
calls `install()`. SWE-bench does not go through Harbor -- it runs `mini-extra swebench` as its own
subprocess -- so before this module the flag reached `ResolvedSpec` and stopped there. It would
have been accepted, recorded in the resolved spec, and done nothing, which is the worst possible
failure for this particular feature: a refusal and a fallback-served answer are both HTTP 200, so
neither an error rate nor a harness log would have shown that it never installed.

The patch has to run INSIDE the subprocess that makes the LLM calls, before the first one, so this
mirrors the Harbor idiom exactly: a bootstrap invoked in place of the console script, forwarding
every argument unchanged.

    uv run python .../mini_swe_agent_bootstrap.py --subset ... --filter ... --config ...

It runs the official `minisweagent.run.benchmarks.swebench` app -- the same Typer command
`mini-extra swebench` dispatches to, with the same arguments and configs -- so the agent itself is
untouched. Both installs are no-ops when their environment variable is unset.
"""

from __future__ import annotations

import json
import os
import sys

# Running a file by path puts its own directory on sys.path[0]. This package has sibling modules
# whose names could shadow real packages for the code we are about to import; drop that entry, as
# harbor_cost_guard does. The engine itself is imported from the installed project.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [entry for entry in sys.path if os.path.abspath(entry or os.getcwd()) != _HERE]

from agent_benchmark.harnesses.anthropic_fallback import (  # noqa: E402
    configured_fallbacks,
)
from agent_benchmark.harnesses.anthropic_fallback import (  # noqa: E402
    install as install_anthropic_fallback,
)
from agent_benchmark.harnesses.openai_fallback import (  # noqa: E402
    configured_fallbacks as configured_openai_fallbacks,
)
from agent_benchmark.harnesses.openai_fallback import (  # noqa: E402
    install as install_openai_fallback,
)


def install_fallbacks() -> None:
    """Install whichever provider fallback the run asked for. Announce it, or say it is off.

    The announcement is not decoration. Because a refusal is an HTTP 200, a run with the flag set
    and the patch missing looks exactly like a run where nothing ever refused. Printing what was
    installed is the only cheap way to tell those apart in the execute log.
    """
    fallbacks = configured_fallbacks()
    if fallbacks is not None:
        installed = install_anthropic_fallback(fallbacks)
        print(
            f"[fallback] anthropic server-side fallback "
            f"{'enabled' if installed else 'FAILED TO INSTALL'}: {json.dumps(fallbacks)}",
            file=sys.stderr,
            flush=True,
        )

    openai_fallbacks = configured_openai_fallbacks()
    if openai_fallbacks is not None:
        install_openai_fallback(openai_fallbacks)
        print(
            f"[openai-fallback] client-side model fallback enabled: {json.dumps(openai_fallbacks)}",
            file=sys.stderr,
            flush=True,
        )


def main() -> None:
    install_fallbacks()
    # Imported only now: the patches must be in place before litellm is exercised, and importing
    # the runner pulls litellm in.
    from minisweagent.run.benchmarks.swebench import app

    # A single-command Typer app dispatches straight to that command, so the arguments this
    # bootstrap received are exactly the ones `mini-extra swebench` would have received.
    app()


if __name__ == "__main__":
    main()
