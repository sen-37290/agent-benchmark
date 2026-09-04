from __future__ import annotations

from pathlib import Path

from agent_benchmark.agents.base import AgentAdapter, AgentInvocation, litellm_model_name
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.run.costguard import LIMIT_ENV

TERMINUS_2_VERSION = "2.0.0"


class Terminus2Adapter(AgentAdapter):
    name = "terminus-2"

    def invocation(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        api_key: str,
    ) -> AgentInvocation:
        del run_dir
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
        # Terminus 2 has no dollar limit of its own, so the engine's cost guard enforces one inside
        # the Harbor process. It reads the limit from the environment; see harbor_cost_guard.
        # A benchmark may opt out of the per-task cap entirely, in which case the limit env var is
        # omitted and the guard installs nothing.
        if spec.benchmark.settings.get("enforce_per_task_cost_limit", True):
            process_environment[LIMIT_ENV] = f"{spec.budget.per_task_usd:.6f}"
        return AgentInvocation(
            model_name=model_name,
            kwargs=kwargs,
            environment={},
            process_environment=process_environment,
        )


ADAPTER = Terminus2Adapter()
