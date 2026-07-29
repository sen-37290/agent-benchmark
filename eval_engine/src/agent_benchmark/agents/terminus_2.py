from __future__ import annotations

from pathlib import Path

from agent_benchmark.agents.base import AgentAdapter, AgentInvocation
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError

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
        model_name = spec.model.model_id
        if spec.model.api == "openrouter":
            model_name = f"openrouter/{model_name}"
        kwargs: dict[str, object] = {
            "reasoning_effort": spec.model.reasoning_effort,
            "temperature": 1,
        }
        provider = spec.model.config.get("model", {}).get("model_kwargs", {}).get("provider")
        if spec.model.api == "openrouter" and isinstance(provider, dict):
            kwargs["llm_call_kwargs"] = {"extra_body": {"provider": provider}}
        environment = {spec.model.api_key_env: api_key}
        return AgentInvocation(
            model_name=model_name,
            kwargs=kwargs,
            environment=environment,
            process_environment=environment,
        )


ADAPTER = Terminus2Adapter()
