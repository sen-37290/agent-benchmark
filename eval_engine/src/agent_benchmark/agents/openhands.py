from __future__ import annotations

from pathlib import Path

from agent_benchmark.agents.base import AgentAdapter, AgentInvocation
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError


class OpenHandsAdapter(AgentAdapter):
    """Invocation contract for CyberGym's pinned official OpenHands example."""

    name = "openhands"

    def invocation(self, spec: ResolvedSpec, run_dir: Path, api_key: str) -> AgentInvocation:
        del run_dir
        if spec.model.subject_agent_version != "35b381f3a8f4b5229934515e9f6b479d6d6415ef":
            raise ConfigurationError("CyberGym requires the pinned official OpenHands example")
        model_name = spec.model.model_id
        if spec.model.api == "openrouter":
            model_name = f"openrouter/{model_name}"
        elif spec.model.api != "anthropic":
            raise ConfigurationError(f"unsupported OpenHands transport: {spec.model.api}")
        kwargs: dict[str, object] = {
            "max_iter": 100,
            "timeout": 1200,
            "max_output_tokens": 2048,
            "reasoning_effort": spec.model.reasoning_effort,
            "difficulty": "level1",
        }
        if spec.model.api == "openrouter":
            kwargs["base_url"] = "https://openrouter.ai/api/v1"
        return AgentInvocation(
            model_name=model_name,
            kwargs=kwargs,
            environment={"LLM_API_KEY": api_key},
            process_environment={"LLM_API_KEY": api_key},
        )


ADAPTER = OpenHandsAdapter()
