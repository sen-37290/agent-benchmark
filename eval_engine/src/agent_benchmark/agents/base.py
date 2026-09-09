from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_benchmark.config.schema import ResolvedSpec

# LiteLLM routes on a provider prefix. Model profiles record the bare, provider-facing ID, so the
# prefix is added here rather than being baked into every profile.
_LITELLM_PREFIX = {
    "openrouter": "openrouter",
    "anthropic": "anthropic",
    "openai": "openai",
}


def litellm_model_name(spec: ResolvedSpec) -> str:
    """Return the model ID in the prefixed form LiteLLM expects for this transport."""
    model_id = spec.model.model_id
    prefix = _LITELLM_PREFIX.get(spec.model.api)
    if prefix is None or model_id.startswith(f"{prefix}/"):
        return model_id
    return f"{prefix}/{model_id}"


@dataclass(frozen=True)
class AgentInvocation:
    model_name: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    process_environment: dict[str, str] = field(default_factory=dict)


class AgentAdapter(ABC):
    name: str

    @abstractmethod
    def invocation(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        api_key: str,
    ) -> AgentInvocation: ...
