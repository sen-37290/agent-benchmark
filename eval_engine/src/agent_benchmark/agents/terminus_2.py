from __future__ import annotations

import inspect
import os
from pathlib import Path

from agent_benchmark.agents.base import AgentAdapter, AgentInvocation, litellm_model_name
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.harnesses.anthropic_fallback import FALLBACKS_ENV, LEDGER_ENV
from agent_benchmark.harnesses.harbor_cost_guard import (
    BUDGET_ENV as LLM_RETRY_BUDGET_ENV,
)
from agent_benchmark.harnesses.harbor_cost_guard import (
    DEFAULT_BUDGET_SECONDS as DEFAULT_LLM_RETRY_BUDGET_SECONDS,
)
from agent_benchmark.harnesses.openai_fallback import (
    FALLBACKS_ENV as OPENAI_FALLBACKS_ENV,
)
from agent_benchmark.harnesses.openai_fallback import (
    LEDGER_ENV as OPENAI_LEDGER_ENV,
)
from agent_benchmark.run.costguard import LIMIT_ENV

TERMINUS_2_VERSION = "2.0.0"
#: Harbor's own default ceiling on one streaming request. Mirrored here only to keep the retry
#: budget above it; Harbor stays authoritative when the benchmark sets no deadline.
HARBOR_DEFAULT_STREAM_DEADLINE_SECONDS = 1800.0


def _require_harbor_agent_option(option: str, setting: str) -> None:
    """Fail now if the installed Harbor cannot honour an agent kwarg, rather than dropping it.

    Harbor's ``Terminus2.__init__`` ends in ``**kwargs``, so an agent kwarg it does not know is
    accepted and silently discarded. A run configured for a transport the installed Harbor does
    not have would launch, report nothing unusual, and quietly use the old one -- and since the
    artifacts of both are identical, nothing afterwards would reveal it either.
    """
    try:
        from harbor.agents.terminus_2.terminus_2 import Terminus2
    except ImportError:
        # Harbor is an optional extra; the harness that runs it installs it. Nothing to check.
        return
    if option not in inspect.signature(Terminus2.__init__).parameters:
        raise StageError(
            f"{setting} is set but the installed Harbor has no Terminus 2 `{option}` option, "
            "and unknown agent kwargs are silently dropped -- the run would not use it. Move "
            "the harbor pin in eval_engine/pyproject.toml to a revision that includes it."
        )


#: Reasoning efforts OpenAI will not serve on /v1/chat/completions. The endpoint validates the
#: field rather than ignoring it -- an unsupported value is an HTTP 400 `unsupported_value`
#: naming the ones it does accept -- so this is a hard boundary, not a preference.
_CHAT_COMPLETIONS_REJECTED_EFFORTS = frozenset({"max"})


def _apply_responses_api(spec: ResolvedSpec, kwargs: dict[str, object]) -> None:
    """Route Terminus 2 through /v1/responses when the benchmark asks for it.

    The switch is scoped to OpenAI models on purpose. `use_responses_api` makes Harbor call
    `litellm.aresponses` and send a Responses-shaped body; Anthropic has no such endpoint, so
    turning it on benchmark-wide would break every Fable run in the same profile. Keying it to
    the model's API lets one setting cover the gpt-5.6 family and leave the rest alone.

    It also closes the door that produced a whole cohort of mismeasured runs. `max` is a hard
    HTTP 400 on chat completions, and the SWE runs that asked for it were served `medium`
    instead by a client-side mapper that dropped the value -- 72,188 of 72,188 responses, none
    of them distinguishable from a correct one. An effort chat completions cannot serve is
    therefore refused up front rather than left to fail, or worse, not fail.
    """
    if spec.model.api != "openai":
        return
    effort = spec.model.reasoning_effort
    if spec.benchmark.settings.get("use_responses_api", False):
        _require_harbor_agent_option("use_responses_api", "use_responses_api")
        kwargs["use_responses_api"] = True
        return
    if effort in _CHAT_COMPLETIONS_REJECTED_EFFORTS:
        raise StageError(
            f"reasoning effort {effort!r} is not available on chat completions for "
            f"{spec.model.model_id} -- OpenAI rejects it with HTTP 400 unsupported_value. Set "
            "use_responses_api on the benchmark profile to reach it, or choose an effort chat "
            "completions serves."
        )


class Terminus2Adapter(AgentAdapter):
    name = "terminus-2"

    def invocation(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        api_key: str,
    ) -> AgentInvocation:
        if spec.model.subject_agent_version != TERMINUS_2_VERSION:
            raise StageError(
                "Terminus 2 version mismatch: "
                f"expected {TERMINUS_2_VERSION}, got {spec.model.subject_agent_version}"
            )
        model_name = litellm_model_name(spec)
        kwargs: dict[str, object] = {"temperature": 1}
        if spec.model.reasoning_effort is not None:
            kwargs["reasoning_effort"] = spec.model.reasoning_effort
        provider = spec.model.config.get("model", {}).get("model_kwargs", {}).get("provider")
        if spec.model.api == "openrouter" and isinstance(provider, dict):
            kwargs["llm_call_kwargs"] = {"extra_body": {"provider": provider}}
        _apply_responses_api(spec, kwargs)
        # Streaming transport, off unless the benchmark asks for it.
        #
        # Terminus 2 issues every model call as one non-streaming request, so on a long
        # generation the socket carries no application bytes for minutes at a time -- which is
        # indistinguishable from a flow an intermediary has silently dropped, and is what left
        # a six-task Fable run with zero completions after 108 minutes while three of its six
        # connections sat frozen at the TLS handshake. Streaming keeps bytes moving and turns
        # the read timeout into an inter-byte idle timer. Harbor consumes the stream internally
        # and still returns one complete response, so nothing else about the run changes.
        stream_deadline_seconds = HARBOR_DEFAULT_STREAM_DEADLINE_SECONDS
        if spec.benchmark.settings.get("stream_llm_calls", False):
            _require_harbor_agent_option("stream", "stream_llm_calls")
            kwargs["stream"] = True
            idle_seconds = spec.benchmark.settings.get("stream_idle_timeout_seconds")
            if idle_seconds is not None:
                kwargs["stream_idle_timeout_seconds"] = float(idle_seconds)
            configured_deadline = spec.benchmark.settings.get("stream_deadline_seconds")
            if configured_deadline is not None:
                stream_deadline_seconds = float(configured_deadline)
                kwargs["stream_deadline_seconds"] = stream_deadline_seconds
        # The provider key is deliberately HOST-ONLY, and `environment` stays empty.
        #
        # Terminus 2 issues its LLM calls from the Harbor process on the host: it builds its
        # LiteLLM client there with no explicit api_key, and litellm reads the key out of
        # os.environ -- which run_logged supplies from `process_environment`. The container runs
        # only the tmux terminal the agent drives, and Harbor routes `environment` (--ae) into
        # `config.agent.env`, which reaches nothing but that container. So the container never
        # needs the key.
        #
        # Sending it anyway leaked it. `--ae KEY=value` becomes literal text in the tmux pane's
        # start command, which puts the key on an argv the agent can read with `ps` and in the
        # session's shell environment it can read with `env` -- and `pipe-pane` records the pane
        # into agent/terminus_2.pane and agent/trajectory.json, so any task whose agent happened
        # to introspect processes or environment published the key in its artifacts. It leaked in
        # 5 of 712 task directories that way, which was luck: the key was readable by every task.
        # Keeping it host-only removes the exposure at the source, independently of how Harbor
        # chooses to seed the tmux session.
        process_environment = {spec.model.api_key_env: api_key}
        if kwargs.get("stream"):
            # One stalled attempt must not consume the whole retry budget. Harbor bounds a single
            # streaming request by its wall-clock deadline and raises litellm.Timeout, which the
            # cost guard classifies as transient and retries -- but only while the budget for the
            # logical turn has time left in it. With both at 1800s the first deadline would use
            # every second of it and the retry would never happen, turning a recoverable stall
            # into a failed trial. Leave an explicit setting alone; it is the operator's call.
            required_budget = stream_deadline_seconds * 3
            already_configured = os.environ.get(LLM_RETRY_BUDGET_ENV, "").strip()
            if not already_configured and required_budget > DEFAULT_LLM_RETRY_BUDGET_SECONDS:
                process_environment[LLM_RETRY_BUDGET_ENV] = f"{required_budget:.0f}"
        # Terminus 2 has no dollar limit of its own, so the engine's cost guard enforces one inside
        # the Harbor process. It reads the limit from the environment; see harbor_cost_guard.
        # A benchmark may opt out of the per-task cap entirely, in which case the limit env var is
        # omitted and the guard installs nothing.
        if spec.benchmark.settings.get("enforce_per_task_cost_limit", True):
            process_environment[LIMIT_ENV] = f"{spec.budget.per_task_usd:.6f}"
        # Anthropic server-side fallback, when the resolved spec asks for it. It is carried on
        # the invocation rather than inherited from the controller's environment: the parameter
        # has to be on every request that can be refused, and ambient state is exactly how one
        # code path ends up unprotected. harbor_cost_guard reads these inside the Harbor process.
        if spec.model.anthropic_fallbacks:
            process_environment[FALLBACKS_ENV] = spec.model.anthropic_fallbacks
            # A refusal is an HTTP 200 and a fallback-served answer looks like any other, so
            # neither appears in Harbor's logs or in any error rate. The ledger is the only
            # record of how often the fallback actually fired.
            process_environment[LEDGER_ENV] = str(run_dir / "logs" / "anthropic_fallback.jsonl")
        # OpenAI has no server-side fallback, so a refused request is retried on the next model
        # client-side; see openai_fallback. Carried on the invocation for the same reason as the
        # Anthropic routing: a fallback that is a property of the request must never depend on
        # ambient controller environment, or one code path ends up unprotected.
        if spec.model.openai_fallbacks:
            process_environment[OPENAI_FALLBACKS_ENV] = spec.model.openai_fallbacks
            # A content-policy refusal is a 400 that leaves no successful-call record, and a
            # fallback-served answer is indistinguishable downstream. The ledger is the only
            # record of which model actually served each call.
            process_environment[OPENAI_LEDGER_ENV] = str(run_dir / "logs" / "openai_fallback.jsonl")
        return AgentInvocation(
            model_name=model_name,
            kwargs=kwargs,
            environment={},
            process_environment=process_environment,
        )


ADAPTER = Terminus2Adapter()
