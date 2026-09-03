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
        environment = {spec.model.api_key_env: api_key}
        # Terminus 2 has no dollar limit of its own, so the engine's cost guard enforces one inside
        # the Harbor process. It reads the limit from the environment; see harbor_cost_guard.
        process_environment = {
            **environment,
            LIMIT_ENV: f"{spec.budget.per_task_usd:.6f}",
        }
        return AgentInvocation(
            model_name=model_name,
            kwargs=kwargs,
            environment=environment,
            process_environment=process_environment,
        )


ADAPTER = Terminus2Adapter()
