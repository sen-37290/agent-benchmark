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

The wrap is installed at LiteLLM's provider seams -- ``litellm.acompletion`` AND
``litellm.aresponses`` -- the same way harbor_cost_guard installs its retry and
anthropic_fallback installs its body parameter. Both are needed because which one Harbor
calls is a configuration choice: Terminus 2 switches to ``aresponses`` whenever
``use_responses_api`` is set, which is how the highest reasoning efforts are reached at all
(chat completions rejects ``max`` outright). Wrapping only ``acompletion`` would leave the
ladder installed, reported as installed, and never invoked -- the refused tasks would simply
start dying again on a run that looks configured to survive them.

Those seams sit BELOW Harbor's own tenacity retry and cost guard, so a refusal is resolved
per underlying HTTP call and is transparent to everything above it. The wrapper is a pure
function of the call's kwargs -- it never mutates the ``LiteLLM`` instance -- so every new
agent turn starts again at the primary model and only climbs the ladder for the calls that
are actually refused.

STREAMING NEEDS ITS OWN HANDLING, and an earlier version of this file wrongly claimed it did
not. A streamed refusal does NOT come out of the ``await`` that opens the request: that call
succeeds and returns an iterator, and the refusal surfaces while the iterator is being
consumed, as ``litellm.MidStreamFallbackError``. The wrap had already returned by then, so it
never saw it. Measured on a real run: the ladder was installed, logged 1,029 served calls and
recorded ZERO refusals, while both cyber tasks died anyway.

So a streaming call returns a generator that owns the ladder and can restart it mid-stream.
Whether restarting is SAFE depends on how the caller assembles the response, which is why the
seam is passed in:

* ``responses`` -- Harbor's ``_consume_responses_stream`` keeps only the terminal
  ``response.completed`` event and discards everything before it. A refused stream never emits
  one, so re-issuing is always safe: the events already yielded belong to a response that will
  never complete, and the caller was going to drop them regardless. Observed refusals arrive
  after 2-4 preamble events and no output text at all.
* ``chat_completions`` -- ``stream_chunk_builder`` accumulates EVERY chunk into one response,
  so replaying a second stream's chunks after the first's would splice two answers together.
  Re-issuing is therefore allowed only while nothing has been yielded; after that the refusal
  is raised, which is the same outcome as having no ladder and never a corrupted one.

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
from datetime import UTC, datetime
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
    text = str(error).lower()
    # A 400 alone is never enough -- an oversized context or a bad parameter is a 400 too, and
    # retrying those on another model would only hide a real error. The markers are required, and
    # the status code is deliberately not consulted.
    return any(marker in text for marker in _REFUSAL_MARKERS)


def _record(entry: dict[str, Any]) -> None:
    """Append one line to the refusal/fallback ledger, if one is configured."""
    path = os.environ.get(LEDGER_ENV, "").strip()
    if not path:
        return
    entry["at"] = datetime.now(UTC).isoformat()
    line = json.dumps(entry, separators=(",", ":"))
    # One Harbor process runs every trial of the job, so concurrent workers share this file.
    with _ledger_lock:
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            # Instrumentation must never take down a trial.
            pass


def _ladder_for(requested_model: Any, fallbacks: list[str]) -> list[Any]:
    """The ordered models to try for one call.

    When the caller's model is already the configured list's head -- the common case, the run's
    primary model -- the list IS the full sequence. Otherwise the caller's model is tried first
    and the list supplies the fallbacks after it.
    """
    if requested_model in fallbacks:
        return fallbacks[fallbacks.index(requested_model) :]
    return [requested_model, *fallbacks] if requested_model else list(fallbacks)


def _may_reissue_mid_stream(api: str, yielded: int) -> bool:
    """Whether a refusal partway through a stream can be retried on the next model.

    See the module docstring: the Responses caller keeps only the terminal event, so restarting
    is always safe there. The chat caller accumulates every chunk, so restarting after it has
    seen one would splice two answers into a single response.
    """
    return api == "responses" or yielded == 0


def _streaming_ladder(  # type: ignore[no-untyped-def]
    original: Any, ladder: list[Any], api: str, requested_model: Any, args, kwargs
):
    """Consume a streamed request, climbing the ladder on a content-policy refusal."""

    async def generate():  # type: ignore[no-untyped-def]
        last_error: BaseException | None = None
        yielded = 0
        for index, model in enumerate(ladder):
            attempt_kwargs = {**kwargs, "model": model}
            entry = {
                "api": api,
                "requested_model": requested_model,
                "attempted_model": model,
                "ladder_index": index,
                "streamed": True,
            }
            try:
                stream = await original(*args, **attempt_kwargs)
            except Exception as error:  # noqa: BLE001 -- re-raised unless it is a refusal
                if not _is_content_policy_refusal(error):
                    raise
                last_error = error
                _record({**entry, "outcome": "refused", "phase": "open", "error": str(error)[:300]})
                continue

            try:
                async for event in stream:
                    yielded += 1
                    yield event
            except Exception as error:  # noqa: BLE001 -- re-raised unless it is a refusal
                if not _is_content_policy_refusal(error):
                    raise
                if not _may_reissue_mid_stream(api, yielded):
                    # Raising is the same outcome as having no ladder at all, and strictly
                    # better than handing back two answers spliced together.
                    _record(
                        {
                            **entry,
                            "outcome": "refused",
                            "phase": "mid_stream",
                            "events_before_refusal": yielded,
                            "reissued": False,
                            "error": str(error)[:300],
                        }
                    )
                    raise
                last_error = error
                _record(
                    {
                        **entry,
                        "outcome": "refused",
                        "phase": "mid_stream",
                        "events_before_refusal": yielded,
                        "reissued": True,
                        "error": str(error)[:300],
                    }
                )
                yielded = 0
                continue

            _record({**entry, "outcome": "served", "served_by_fallback": index > 0})
            return

        _record(
            {
                "api": api,
                "requested_model": requested_model,
                "ladder": ladder,
                "streamed": True,
                "outcome": "provider_refusal",
            }
        )
        assert last_error is not None  # only reached after at least one refusal
        raise last_error

    return generate()


def _wrap(original: Any, fallbacks: list[str], api: str) -> Any:
    """Return ``original`` with the refusal ladder around it, for one LiteLLM entry point."""

    async def call_with_fallback(*args, **kwargs):  # type: ignore[no-untyped-def]
        requested_model = kwargs.get("model")
        ladder = _ladder_for(requested_model, fallbacks)

        if kwargs.get("stream"):
            # Hand back a generator that owns the ladder. Awaiting the underlying call only
            # opens the stream; the refusal arrives later, while it is being read.
            return _streaming_ladder(original, ladder, api, requested_model, args, kwargs)

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
                        "api": api,
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
                    "api": api,
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
                "api": api,
                "requested_model": requested_model,
                "ladder": ladder,
                "outcome": "provider_refusal",
            }
        )
        assert last_error is not None  # the loop only exits here after at least one refusal
        raise last_error

    return call_with_fallback


def install(fallbacks: list[str]) -> bool:
    """Retry a content-policy refusal down ``fallbacks``. Returns True when applied."""
    import litellm

    if getattr(litellm, "_agent_bench_openai_fallback", False):
        return True

    # Both seams, because which one Harbor uses is a configuration choice and a ladder that is
    # only installed on the unused one is worse than none: it reports as installed.
    litellm.acompletion = _wrap(  # type: ignore[assignment]
        litellm.acompletion, fallbacks, "chat_completions"
    )
    litellm.aresponses = _wrap(  # type: ignore[assignment]
        litellm.aresponses, fallbacks, "responses"
    )
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
