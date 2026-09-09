"""OpenHands launcher that pins the OpenRouter upstream provider, repairs Friendli's
known malformed tool-call format, and falls back to a second provider if repair fails.

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
body dict -- to inject a ``provider`` block, then hands control to ``openhands.core.main``
exactly as ``python -m`` would. Patching a class method (looked up at call time) is immune
to OpenHands binding ``litellm.completion`` by reference at import.

Repairing the malformed tool call
---------------------------------
Friendli intermittently serves GLM-5.3 tool calls as ``<tool_call>function=NAME>`` instead
of OpenHands' required ``<function=NAME>`` -- a guided-generation artefact that drops the
leading ``<`` and inserts a ``<tool_call>`` wrapper (and also drops the closing
``</function>``). Friendli returns HTTP 200 with ``finish_reason: stop`` for these, so
OpenRouter treats them as a success and never fails over; the defect is only visible in the
response *content*. OpenHands' non-native parser (``<function=([^>]+)>...</function>``)
cannot read it, so the response yields no action, and three such non-actions trip the stuck
detector and abort the task with no PoC.

Because the malformation is deterministic, we repair it in place: we rewrite
``<tool_call>function=`` to ``<function=`` and restore the closing tag, then hand the
corrected content back to OpenHands' unmodified pipeline. This keeps the Friendli response
we already paid for -- no second API call -- and uses the (cheaper/faster but unstable)
Friendli route maximally.

Recovery (malformed and empty responses)
-----------------------------------------
Two primary-provider failure modes are recovered, both via an optional fallback provider
configured through ``CYBERGYM_OPENROUTER_FALLBACK_PROVIDER`` (e.g. ``{"only": ["z-ai"]}``):

* *Unrepairable tool call* -- content that clearly means a tool call but is not the known
  repairable malformation. We re-issue the identical completion on the fallback provider.
* *Empty/errored response* -- Friendli intermittently ends a completion with
  ``finish_reason=error`` after emitting only reasoning tokens, leaving empty content and no
  tool call. OpenHands would turn this into an empty message and, after three in a row, abort
  the task with ``AgentStuckInLoopError``. We first re-roll the primary once (the failure is
  transient, so it usually succeeds and stays on the preferred provider), then fall back.

At most three upstream calls per completion (primary, one primary re-roll, one fallback), so
there is no loop and the extra cost is bounded.

If ``CYBERGYM_OPENROUTER_PROVIDER`` is unset this is a transparent passthrough, so the
harness can always launch through it. Repair and fallback install only for OpenRouter runs.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import runpy
import sys
from pathlib import Path

# Per-completion provider override. ``transform_request`` injects whatever this holds,
# defaulting to the primary pin. The fallback path sets it for the retry call only.
_current_provider: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "cybergym_openrouter_provider", default=None
)

# A well-formed OpenHands non-native tool call. If this matches, the response is fine.
_WELL_FORMED_FUNCTION = re.compile(r"<function=[^>]+>")


def _load_provider(env_name: str) -> dict | None:
    raw = os.environ.get(env_name)
    if not raw:
        return None
    try:
        provider = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(provider, dict) or not provider:
        return None
    return provider


def _log(message: str) -> None:
    print(f"[cybergym-provider-pin] {message}", file=sys.stderr, flush=True)


def _response_message(resp: object):
    """The assistant message object of a completion, or None if not inspectable.

    None means "do not touch" (e.g. native ``tool_calls`` already present, or an
    unexpected response shape).
    """
    try:
        message = resp.choices[0].message  # type: ignore[attr-defined]
    except (AttributeError, IndexError, TypeError):
        return None
    if getattr(message, "tool_calls", None):
        return None
    return message


def _repair_malformed_tool_call(content: str) -> str | None:
    """Repair Friendli's known malformed tool call, or None if it is not that case.

    The artefact is ``<tool_call>function=NAME>`` (the ``<`` of ``<function=`` dropped and a
    ``<tool_call>`` wrapper inserted), usually also missing the trailing ``</function>``.
    """
    if "<tool_call>function=" not in content:
        return None
    repaired = content.replace("<tool_call>function=", "<function=")
    # Restore the closing tag OpenHands' regex requires (mirrors its own _fix_stopword).
    if "<function=" in repaired and "</function>" not in repaired:
        repaired = repaired.rstrip() + "\n</function>"
    return repaired


def _intends_tool_call(content: str) -> bool:
    """Whether content clearly meant a tool call OpenHands' parser cannot read."""
    if _WELL_FORMED_FUNCTION.search(content):
        return False
    if "<tool_call>" in content:
        return True
    return "function=" in content and "<parameter=" in content


def _classify_and_repair(resp: object) -> str:
    """Classify a completion, repairing a malformed tool call in place when possible.

    Returns one of:
    * ``"ok"``      -- native tool_calls present, a well-formed/repaired ``<function=>`` call,
                       or legitimate non-empty assistant text (a final answer).
    * ``"empty"``   -- no tool call and no content. The primary (Friendli) intermittently ends
                       a completion with ``finish_reason=error`` after emitting only reasoning
                       tokens, yielding empty content; OpenHands would turn this into an empty
                       message and, after three in a row, abort with AgentStuckInLoopError.
    * ``"unrepairable"`` -- content clearly intends a tool call this layer cannot repair.
    """
    message = _response_message(resp)
    if message is None:
        return "ok"  # native tool_calls already present
    content = getattr(message, "content", None) or ""
    if _WELL_FORMED_FUNCTION.search(content):
        return "ok"
    repaired = _repair_malformed_tool_call(content)
    if repaired is not None and _WELL_FORMED_FUNCTION.search(repaired):
        try:
            message.content = repaired
            _log("repaired malformed tool call in place (kept primary provider)")
            return "ok"
        except Exception as exc:  # pragma: no cover - defensive
            _log(f"could not rewrite repaired content ({exc!r}); recovering")
    if not content.strip():
        return "empty"
    if _intends_tool_call(content):
        return "unrepairable"
    return "ok"  # legitimate non-empty final answer / plain text


def _install_provider_pin() -> dict | None:
    """Patch ``transform_request`` to inject the current provider block. Returns primary."""
    primary = _load_provider("CYBERGYM_OPENROUTER_PROVIDER")
    if primary is None:
        return None

    # Import triggers the full LiteLLM import chain; safe at normal runtime (unlike site
    # initialisation). OpenHands' later ``import litellm`` reuses this cached module.
    from litellm.llms.openrouter.chat.transformation import OpenrouterConfig

    original = OpenrouterConfig.transform_request

    def transform_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        body = original(self, *args, **kwargs)
        if isinstance(body, dict):
            body["provider"] = _current_provider.get() or primary
        return body

    OpenrouterConfig.transform_request = transform_request  # type: ignore[assignment]
    return primary


#: Comma-separated request parameters to remove from every LiteLLM call, set by the harness.
DROP_PARAMS_ENV = "AGENT_BENCH_DROP_PARAMS"


def _install_param_filter() -> None:
    """Strip request parameters the target model refuses, before the first call.

    claude-fable-5-1 (and other current models) return
    ``400 invalid_request_error: `temperature` is deprecated for this model``. OpenHands sends
    ``temperature`` from an LLMConfig default on every request and offers no way to omit it, so
    every CyberGym task died on its first LLM call: 300 tasks "completed" in minutes with no PoC
    and no spend.

    Reacting to the rejection and retrying was tried first and proved unreliable -- the error
    still surfaced to OpenHands' controller, which puts the agent into an error state before a
    retry can help. Removing the parameter up front is deterministic: the bad request is never
    issued. ``AGENT_BENCH_DROP_PARAMS`` lists the names, so OpenRouter runs (where temperature is
    accepted and was used by previous experiments) are unaffected.
    """
    import litellm

    names = [
        name.strip() for name in os.environ.get(DROP_PARAMS_ENV, "").split(",") if name.strip()
    ]
    if not names:
        return

    original_completion = litellm.completion

    def completion_without_params(*args, **kwargs):  # type: ignore[no-untyped-def]
        for name in names:
            kwargs.pop(name, None)
        return original_completion(*args, **kwargs)

    litellm.completion = completion_without_params  # type: ignore[assignment]
    _log(f"dropping request params before every call: {', '.join(names)}")


def _install_cost_guard() -> bool:
    """Meter every LLM call and stop the task at its per-task dollar limit.

    OpenHands has no cost ceiling of its own (``max_iter`` is deliberately unbounded for this
    benchmark), and CyberGym results carried no cost at all before this. Wrapping
    ``litellm.completion`` gives both: a per-call usage ledger and the limit.

    Install this BEFORE the provider pin so the repair/fallback wrapper ends up outside it --
    then each physical request, including re-rolls and fallback retries, is metered exactly once.
    """
    import litellm

    # This script runs under OpenHands' own Poetry venv, where agent_benchmark is not installed.
    # The engine's src/ directory is two levels above this file; add it so the shared meter can be
    # imported without vendoring a second copy of the pricing logic.
    engine_src = str(Path(__file__).resolve().parents[2])
    if engine_src not in sys.path:
        sys.path.insert(0, engine_src)
    from agent_benchmark.run.costguard import CostMeter

    meter = CostMeter()
    if meter.limit_usd <= 0 and meter.usage_log is None:
        return False

    original_completion = litellm.completion

    def completion_with_meter(*args, **kwargs):  # type: ignore[no-untyped-def]
        # Refuse the call outright once the limit is reached, so a task cannot exceed it by one
        # more request. The raised error ends this task only; the harness records COST_LIMIT.
        meter.enforce()
        resp = original_completion(*args, **kwargs)
        meter.record_and_enforce(resp, kwargs.get("model"))
        return resp

    litellm.completion = completion_with_meter  # type: ignore[assignment]
    limit = f"${meter.limit_usd:.2f}" if meter.limit_usd > 0 else "disabled"
    _log(f"cost guard installed (per-task limit {limit}, ledger {meter.usage_log})")
    return True


def _install_completion_repair(primary: dict) -> None:
    """Wrap ``litellm.completion`` to repair malformed tool calls and, if that fails,
    retry on the fallback provider.

    Must run before ``openhands.llm.llm`` is imported: it binds ``from litellm import
    completion`` at import time, so we replace the module attribute first and OpenHands then
    binds our wrapper. ``runpy.run_module`` (which imports OpenHands) runs after this.
    """
    import litellm

    fallback = _load_provider("CYBERGYM_OPENROUTER_FALLBACK_PROVIDER")
    if fallback == primary:
        fallback = None

    original_completion = litellm.completion

    def completion_with_recovery(*args, **kwargs):  # type: ignore[no-untyped-def]
        # Streaming is disabled in this harness, so ``resp`` is always a full response.
        resp = original_completion(*args, **kwargs)
        outcome = _classify_and_repair(resp)  # repairs a malformed tool call in place
        if outcome == "ok":
            return resp

        # An empty/errored response (no tool call, no content) is usually a transient
        # Friendli interruption mid-reasoning. Re-roll the primary once before spending a
        # fallback call -- the failure is random, so a re-roll usually succeeds and keeps the
        # request on the preferred provider.
        if outcome == "empty":
            _log("primary returned an empty/errored response; re-rolling primary once")
            resp = original_completion(*args, **kwargs)
            outcome = _classify_and_repair(resp)
            if outcome == "ok":
                return resp

        # Still unusable (empty after the re-roll, or a tool call this layer cannot repair):
        # re-issue the identical request on the fallback provider if one is configured.
        if fallback is not None:
            _log(
                f"recovering unusable response (reason={outcome}) on fallback provider "
                f"{json.dumps(fallback)}"
            )
            token = _current_provider.set(fallback)
            try:
                resp = original_completion(*args, **kwargs)
            finally:
                _current_provider.reset(token)
            _classify_and_repair(resp)  # repair a (rare) malformed fallback response in place
        return resp

    litellm.completion = completion_with_recovery  # type: ignore[assignment]


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
    # ORDER MATTERS: install the provider pin and the completion repair BEFORE anything
    # imports OpenHands. ``_install_completion_repair`` replaces ``litellm.completion``, and
    # ``openhands.llm.llm`` binds ``from litellm import completion`` by reference at import
    # time (line ~18) -- so the repair must be in place before that import runs, or the
    # wrapper is bypassed and Friendli's malformed tool calls reach the agent unrepaired.
    # ``_install_waiting_poll_stuck_fix`` imports ``openhands.controller.stuck`` (which pulls
    # in ``openhands.llm.llm``), so it MUST come last, after the completion repair is live.
    # The cost guard goes on first so the repair/fallback wrapper sits outside it and every
    # physical request -- original, re-roll and fallback -- is metered exactly once. It is
    # installed for every transport, not just OpenRouter.
    # Innermost first: the param-drop retry must wrap the real API call, so a request the
    # provider refuses is re-issued before the cost meter or the repair layer ever see it.
    _install_param_filter()
    _install_cost_guard()
    primary = _install_provider_pin()
    if primary is not None:
        _install_completion_repair(primary)
    _install_waiting_poll_stuck_fix()
    # Mirror ``python -m openhands.core.main``: the working directory is the OpenHands
    # repo, and ``-m`` puts the cwd on sys.path[0]. run_controller reads its arguments
    # from sys.argv, which already carries everything after this script's path.
    sys.path.insert(0, "")
    runpy.run_module("openhands.core.main", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
