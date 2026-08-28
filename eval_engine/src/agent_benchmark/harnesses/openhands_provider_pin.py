"""OpenHands launcher that pins the OpenRouter upstream provider.

CyberGym runs the pinned official OpenHands example, which builds the LiteLLM request
itself and strips ``extra_body`` for every non-``litellm_proxy`` model (see
``openhands/llm/llm.py``). ``LLMConfig`` also forbids unknown fields, so there is no
supported seam in the config file to carry OpenRouter's ``provider`` routing block.

Without that block OpenRouter is free to route ``z-ai/glm-5.3`` to *any* upstream
provider, and it does: a single recorded run was served by five different providers
(Friendli, Z.AI, BaseTen, Cloudflare, GMICloud). Different providers format tool calls
differently, which is exactly the flakiness we need to remove.

This launcher patches the one stable LiteLLM seam that always runs for OpenRouter chat
completions -- ``OpenrouterConfig.transform_request``, which returns the final request
body dict -- to inject the ``provider`` block read from ``CYBERGYM_OPENROUTER_PROVIDER``,
then hands control to ``openhands.core.main`` exactly as ``python -m`` would. Patching a
class method (looked up at call time) is immune to OpenHands binding ``litellm.completion``
by reference at import.

If ``CYBERGYM_OPENROUTER_PROVIDER`` is unset this is a transparent passthrough, so the
harness can always launch through it.
"""

from __future__ import annotations

import json
import os
import runpy
import sys


def _install_provider_pin() -> None:
    raw = os.environ.get("CYBERGYM_OPENROUTER_PROVIDER")
    if not raw:
        return
    try:
        provider = json.loads(raw)
    except ValueError:
        return
    if not isinstance(provider, dict) or not provider:
        return

    # Import triggers the full LiteLLM import chain; safe at normal runtime (unlike site
    # initialisation). OpenHands' later ``import litellm`` reuses this cached module.
    from litellm.llms.openrouter.chat.transformation import OpenrouterConfig

    original = OpenrouterConfig.transform_request

    def transform_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        body = original(self, *args, **kwargs)
        if isinstance(body, dict):
            body["provider"] = provider
        return body

    OpenrouterConfig.transform_request = transform_request  # type: ignore[assignment]


def main() -> None:
    _install_provider_pin()
    # Mirror ``python -m openhands.core.main``: the working directory is the OpenHands
    # repo, and ``-m`` puts the cwd on sys.path[0]. run_controller reads its arguments
    # from sys.argv, which already carries everything after this script's path.
    sys.path.insert(0, "")
    runpy.run_module("openhands.core.main", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
