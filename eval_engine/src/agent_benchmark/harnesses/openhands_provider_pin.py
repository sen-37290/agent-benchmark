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


def _install_waiting_poll_stuck_fix() -> None:
    """Stop the loop detector from killing an agent that is legitimately *waiting* on a
    still-running command.

    The sandbox bash session hands control back after a soft timeout with a prompt telling
    the model to "send empty command '' to wait longer" (see NO_CHANGE_TIMEOUT_SECONDS).
    The model does exactly that, gets the identical "no new output" observation each time,
    and after four such identical action/observation pairs scenario 1 of the stuck detector
    (``_is_stuck_repeating_action_observation``) raises ``AgentStuckInLoopError`` -- aborting
    the task with no PoC. Following the prompt's own advice must not count as a loop.

    We leave the detector fully intact for every genuine repeated-action loop and exempt
    only the narrow "empty ``is_input`` poll" case. This is a belt-and-braces guard: raising
    NO_CHANGE_TIMEOUT_SECONDS already lets slow commands finish in one turn so the model
    rarely has to poll at all. Duck-typed on the action so it survives OpenHands drift.
    """
    try:
        from openhands.controller.stuck import StuckDetector
    except Exception as exc:  # pragma: no cover - version-drift guard
        _log(f"waiting-poll stuck fix skipped (import failed: {exc!r})")
        return

    original = StuckDetector._is_stuck_repeating_action_observation

    def _is_waiting_poll(action: object) -> bool:
        # A blank command sent with is_input=true is "press Enter to keep waiting", not a
        # real action; a genuine stuck loop repeats a content-bearing command instead.
        if not getattr(action, "is_input", False):
            return False
        command = getattr(action, "command", None)
        return isinstance(command, str) and command.strip() == ""

    def patched(self, last_actions, last_observations):  # type: ignore[no-untyped-def]
        if len(last_actions) == 4 and all(_is_waiting_poll(a) for a in last_actions):
            return False
        return original(self, last_actions, last_observations)

    StuckDetector._is_stuck_repeating_action_observation = patched  # type: ignore[assignment]
    _log("installed stuck-detector exemption for empty is_input waiting polls")


def main() -> None:
    _install_waiting_poll_stuck_fix()
    _install_provider_pin()
    # Mirror ``python -m openhands.core.main``: the working directory is the OpenHands
    # repo, and ``-m`` puts the cwd on sys.path[0]. run_controller reads its arguments
    # from sys.argv, which already carries everything after this script's path.
    sys.path.insert(0, "")
    runpy.run_module("openhands.core.main", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
