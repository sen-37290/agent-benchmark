"""Subject-agent adapters selected by resolved agent profile names."""

from importlib import import_module

from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.subject_agents.base import SubjectAgentAdapter


def subject_agent_adapter(name: str) -> SubjectAgentAdapter:
    module_name = name.replace("-", "_")
    try:
        module = import_module(f"agent_benchmark.subject_agents.{module_name}")
        adapter = module.ADAPTER
    except (AttributeError, ModuleNotFoundError) as error:
        raise ConfigurationError(f"subject agent adapter is not available: {name}") from error
    if not isinstance(adapter, SubjectAgentAdapter) or adapter.name != name:
        raise ConfigurationError(f"invalid subject agent adapter: {name}")
    return adapter


__all__ = ["subject_agent_adapter"]
