"""Trace ``reasoning_effort`` from the engine's argv to the bytes on the socket.

WHY THIS EXISTS. Every record we keep of a run's reasoning effort is a record of what was
*asked for*: the resolved spec, ``run_meta.json``, Harbor's per-trial ``config.json``, the
``--ak`` argv. None of them observe what was *sent*. That distinction is not academic --
LiteLLM 1.94.0 accepts ``reasoning_effort="max"`` on the Responses API and silently drops it
(BerriAI/litellm#38084), so a run can record `max` everywhere and be served `medium`. On
chat completions, which is the surface Harbor uses, the value should survive; this probe is
what turns "should" into a log line.

The chat-completions response carries no echo of the effort, unlike the Responses API, so
there is nothing after the fact to audit. The only way to know is to watch the request.

FOUR DEPTHS, because a value can be lost between any two of them:

  A. engine    -- the ``--ak reasoning_effort=`` argv (see harbor._agent_arguments)
  B. harbor    -- ``LiteLLM.call`` entry, and the kwargs Harbor hands ``litellm.acompletion``
  C. litellm   -- ``data`` in ``OpenAIChatCompletion.make_openai_chat_completion_request``,
                  the dict passed straight to the OpenAI SDK
  D. wire      -- the JSON body of the actual ``httpx`` request to api.openai.com

A/B is our own plumbing, B/C is LiteLLM's parameter mapping (where the Responses-API bug
lives), C/D is the OpenAI SDK. Logging only one of them cannot tell you which hop lost it.

Inert unless ``AGENT_BENCH_EFFORT_LOG`` names a file, and every hook is wrapped so that
instrumentation can never break a request: a probe that can fail the run it is measuring is
worse than no probe.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time

LOG_ENV = "AGENT_BENCH_EFFORT_LOG"

_lock = threading.Lock()


def _path() -> str | None:
    return os.environ.get(LOG_ENV) or None


def _emit(stage: str, payload: dict[str, object]) -> None:
    path = _path()
    if not path:
        return
    line = {
        "ts": time.time(),
        "pid": os.getpid(),
        "stage": stage,
        **payload,
    }
    with contextlib.suppress(Exception):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, default=str) + "\n")


def record(stage: str, **payload: object) -> None:
    """Public entry point for call sites outside this module (stage A)."""
    _emit(stage, payload)


def record_at(path: str | None, stage: str, **payload: object) -> None:
    """Write to an explicit path rather than reading the environment.

    The engine hands the execute stage to a worker over SSH, and an SSH login shell inherits
    none of the engine's exported variables -- so a probe gated on the engine's own environment
    is silently off in exactly the process that builds the request. The run directory does cross
    that boundary, so the path is carried explicitly instead.
    """
    if not path:
        return
    previous = os.environ.get(LOG_ENV)
    os.environ[LOG_ENV] = path
    try:
        _emit(stage, payload)
    finally:
        if previous is None:
            os.environ.pop(LOG_ENV, None)
        else:
            os.environ[LOG_ENV] = previous


def _summarise(data: dict[str, object]) -> dict[str, object]:
    """Keep the interesting keys and drop the prompt.

    Messages are the bulk of a terminal-agent request and none of it bears on effort, so only
    a count is kept. `reasoning_effort` and `reasoning` are recorded with a sentinel when
    absent, because "absent" is the failure this probe exists to catch and must be
    distinguishable from "present and null".
    """
    return {
        "model": data.get("model"),
        "reasoning_effort": data.get("reasoning_effort", "<ABSENT>"),
        "reasoning": data.get("reasoning", "<ABSENT>"),
        "temperature": data.get("temperature", "<ABSENT>"),
        "stream": data.get("stream", "<ABSENT>"),
        "has_tools": bool(data.get("tools")),
        "n_messages": (
            len(data.get("messages") or [])
            if isinstance(data.get("messages"), list)
            else None
        ),
        "other_keys": sorted(k for k in data if k not in
                             {"model", "reasoning_effort", "reasoning", "temperature",
                              "stream", "tools", "messages"}),
    }


def install() -> bool:
    """Install the B/C/D hooks. Returns False when the probe is switched off."""
    if not _path():
        return False

    import litellm

    if getattr(litellm, "_agent_bench_effort_probe", False):
        return True

    # -- B1: Harbor's own client, at the point it decides what to send.
    with contextlib.suppress(Exception):
        from harbor.llms.lite_llm import LiteLLM

        original_call = LiteLLM.call

        async def call(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _emit(
                "B1-harbor-LiteLLM.call",
                {
                    "model_name": getattr(self, "_model_name", None),
                    "reasoning_effort": getattr(self, "_reasoning_effort", "<MISSING ATTR>"),
                    "temperature": getattr(self, "_temperature", None),
                    "use_responses_api": getattr(self, "_use_responses_api", None),
                    "max_thinking_tokens": getattr(self, "_max_thinking_tokens", None),
                    "llm_kwargs": getattr(self, "_llm_kwargs", None),
                },
            )
            return await original_call(self, *args, **kwargs)

        LiteLLM.call = call  # type: ignore[method-assign]

    # -- B2: what Harbor actually handed LiteLLM. This is `completion_kwargs`.
    with contextlib.suppress(Exception):
        original_acompletion = litellm.acompletion

        async def acompletion(*args, **kwargs):  # type: ignore[no-untyped-def]
            _emit("B2-litellm.acompletion-in", _summarise(kwargs))
            return await original_acompletion(*args, **kwargs)

        litellm.acompletion = acompletion  # type: ignore[assignment]

    # -- C: LiteLLM's last word before the OpenAI SDK. `data` is the request payload.
    with contextlib.suppress(Exception):
        from litellm.llms.openai.openai import OpenAIChatCompletion

        original_request = OpenAIChatCompletion.make_openai_chat_completion_request

        async def make_openai_chat_completion_request(self, openai_aclient, data, *args, **kwargs):  # type: ignore[no-untyped-def]
            _emit("C-litellm-openai-data", _summarise(data if isinstance(data, dict) else {}))
            return await original_request(self, openai_aclient, data, *args, **kwargs)

        OpenAIChatCompletion.make_openai_chat_completion_request = (  # type: ignore[method-assign]
            make_openai_chat_completion_request
        )

    # -- D: the socket. Nothing downstream of this can change the request.
    with contextlib.suppress(Exception):
        import httpx

        original_send = httpx.AsyncClient.send

        async def send(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
            with contextlib.suppress(Exception):
                if "api.openai.com" in str(request.url) or "/chat/completions" in str(request.url):
                    body = json.loads(request.content)
                    _emit("D-wire", {"url": str(request.url), **_summarise(body)})
            return await original_send(self, request, *args, **kwargs)

        httpx.AsyncClient.send = send  # type: ignore[method-assign]

    litellm._agent_bench_effort_probe = True  # type: ignore[attr-defined]
    return True
