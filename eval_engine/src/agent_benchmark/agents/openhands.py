from __future__ import annotations

from pathlib import Path

from agent_benchmark.agents.base import AgentAdapter, AgentInvocation, litellm_model_name
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import ConfigurationError

# Transports OpenHands may target, and the base URL each one needs. Anthropic uses the SDK
# default, so it carries no explicit URL.
_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": None,
}


class OpenHandsAdapter(AgentAdapter):
    """Invocation contract for CyberGym's pinned official OpenHands example."""

    name = "openhands"

    def invocation(self, spec: ResolvedSpec, run_dir: Path, api_key: str) -> AgentInvocation:
        del run_dir
        if spec.model.subject_agent_version != "35b381f3a8f4b5229934515e9f6b479d6d6415ef":
            raise ConfigurationError("CyberGym requires the pinned official OpenHands example")
        if spec.model.api not in _BASE_URLS:
            raise ConfigurationError(f"unsupported OpenHands transport: {spec.model.api}")
        model_name = litellm_model_name(spec)
        kwargs: dict[str, object] = {
            # No iteration or wall-clock limit: run until the agent finishes or the
            # provider (OpenRouter) key's own quota stops it. timeout=None => no deadline.
            "max_iter": 100_000_000,
            "timeout": None,
            "max_output_tokens": 2048,
            "reasoning_effort": spec.model.reasoning_effort,
            "difficulty": "level1",
        }
        base_url = _BASE_URLS[spec.model.api]
        if base_url is not None:
            kwargs["base_url"] = base_url
        return AgentInvocation(
            model_name=model_name,
            kwargs=kwargs,
            environment={"LLM_API_KEY": api_key},
            process_environment={"LLM_API_KEY": api_key},
        )


ADAPTER = OpenHandsAdapter()
