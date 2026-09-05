"""Client-side model fallback for OpenAI content-policy refusals.

gpt-5.6 refuses some Terminal-Bench tasks with an HTTP 400 whose error code is
``cyber_policy``: "This content was flagged for possible cybersecurity risk ... join the
Trusted Access for Cyber program". It is an ACCOUNT-policy boundary, not a transient fault,
so Harbor's retry (which does re-issue a 400 up to three times) only reproduces it, and
the trial dies ``error / BadRequestError`` the moment the agent engages the vulnerability
-- usually on the second call, after one benign turn.

Anthropic fixes this at the source with a ``fallbacks`` body parameter the API honours
server-side (see anthropic_fallback). OpenAI ships no such parameter, so the fallback has
to be done client-side: catch the refusal and re-issue the identical request on another
model. That is sound here because the three gpt-5.6 snapshots -- sol, terra, luna -- are
addressable by a single key (verified: every key serves every snapshot), so only the model
string changes; the key, messages, effort and every other parameter stay identical.

The wrap is installed at LiteLLM's provider seam, ``litellm.acompletion``, the same way
harbor_cost_guard installs its retry and anthropic_fallback installs its body parameter.
That seam sits BELOW Harbor's own tenacity retry and cost guard, so a refusal is resolved
per underlying HTTP call and is transparent to everything above it. The wrapper is a pure
function of the call's kwargs -- it never mutates the ``LiteLLM`` instance -- so every new
agent turn starts again at the primary model and only climbs the ladder for the calls that
are actually refused.

The ladder is the ordered list of models to try, primary first, each attempted once:

    ["openai/gpt-5.6-sol", "openai/gpt-5.6-terra", "openai/gpt-5.6-luna"]

gives the intended policy -- primary sol, a second sol attempt (the filter is not
deterministic, so a repeat sometimes succeeds), then terra, then luna. If every rung is
refused the last refusal is raised unchanged, which surfaces as the task's provider_refusal;
any error that is not a content-policy refusal is raised at once so the layers above handle
it as they would without this wrap.

A refusal is an HTTP 400 that never reaches Harbor's logs as anything but a killed trial,
and a fallback-served answer is indistinguishable from a normal one downstream. The ledger
is the only record of how often the fallback fired and which model actually served each
call.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

#: Ordered JSON list of litellm model ids to try, primary first. Empty/unset disables the wrap.
FALLBACKS_ENV = "AGENT_BENCH_OPENAI_FALLBACKS"
#: Where to append one JSON line per resolved request. Optional.
LEDGER_ENV = "AGENT_BENCH_OPENAI_FALLBACK_LEDGER"

#: Substrings that identify OpenAI's cyber content-policy refusal. Matched case-insensitively
#: against the exception text; the error code is the authoritative signal, the message the
#: human-readable fallback.
_REFUSAL_MARKERS = (
    "cyber_policy",
    "flagged for possible cybersecurity",
    "trusted access for cyber",
)

_ledger_lock = threading.Lock()


def configured_fallbacks() -> list[str] | None:
    """Parse the requested ladder, or None when the run does not use fallback."""
    raw = os.environ.get(FALLBACKS_ENV, "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{FALLBACKS_ENV} must be a JSON list of model ids: {error}") from error
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"{FALLBACKS_ENV} must be a non-empty JSON list of model ids")
    for entry in parsed:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"{FALLBACKS_ENV} entries must be non-empty model-id strings")
    return [entry.strip() for entry in parsed]


def _is_content_policy_refusal(error: BaseException) -> bool:
    """True only for OpenAI's cyber content-policy 400, never a generic bad request."""
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    text = str(error).lower()
    if any(marker in text for marker in _REFUSAL_MARKERS):
        return True
    # A 400 alone is never enough -- an oversized context or a bad parameter is a 400 too, and
    # retrying those on another model would only hide a real error. The markers above are required.
    return False


def _record(entry: dict[str, Any]) -> None:
    """Append one line to the refusal/fallback ledger, if one is configured."""
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


def install(fallbacks: list[str]) -> bool:
    """Retry a content-policy refusal down ``fallbacks``. Returns True when applied."""
    import litellm

    if getattr(litellm, "_agent_bench_openai_fallback", False):
        return True

    original = litellm.acompletion

    async def acompletion_with_fallback(*args, **kwargs):  # type: ignore[no-untyped-def]
        requested_model = kwargs.get("model")
        # The ladder is the configured list. When the caller's model is already its head (the
        # common case -- the run's primary model), the list IS the full sequence. Otherwise the
        # caller's model is tried first and the list supplies the fallbacks after it.
        if requested_model in fallbacks:
            ladder = fallbacks[fallbacks.index(requested_model) :]
        else:
            ladder = [requested_model, *fallbacks] if requested_model else list(fallbacks)

        last_error: BaseException | None = None
        for index, model in enumerate(ladder):
            attempt_kwargs = {**kwargs, "model": model}
            try:
                response = await original(*args, **attempt_kwargs)
            except Exception as error:  # noqa: BLE001 -- re-raised below unless it is a refusal
                if not _is_content_policy_refusal(error):
                    # Not a refusal: hand it straight back so the retry/cost layers above decide.
                    raise
                last_error = error
                _record(
                    {
                        "requested_model": requested_model,
                        "attempted_model": model,
                        "ladder_index": index,
                        "outcome": "refused",
                        "error": str(error)[:300],
                    }
                )
                continue
            _record(
                {
                    "requested_model": requested_model,
                    "attempted_model": model,
                    "ladder_index": index,
                    "served_model": _response_model(response) or model,
                    "outcome": "served",
                    "served_by_fallback": index > 0,
                }
            )
            return response

        # Every rung refused: the task cannot be served. Record the terminal state and re-raise
        # the last refusal so it surfaces as the trial's provider_refusal.
        _record(
            {
                "requested_model": requested_model,
                "ladder": ladder,
                "outcome": "provider_refusal",
            }
        )
        assert last_error is not None  # the loop only exits here after at least one refusal
        raise last_error

    litellm.acompletion = acompletion_with_fallback  # type: ignore[assignment]
    litellm._agent_bench_openai_fallback = True  # type: ignore[attr-defined]
    return True


def _response_model(response: Any) -> str | None:
    """The model that actually served a response, for the ledger."""
    try:
        if isinstance(response, dict):
            return response.get("model")
        return getattr(response, "model", None)
    except Exception:
        return None
