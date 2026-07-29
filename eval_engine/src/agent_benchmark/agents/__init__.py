"""Agent adapters selected by resolved agent profile names."""

from importlib import import_module

from agent_benchmark.agents.base import AgentAdapter
from agent_benchmark.exceptions import ConfigurationError


def agent_adapter(name: str) -> AgentAdapter:
    module_name = name.replace("-", "_")
    try:
        module = import_module(f"agent_benchmark.agents.{module_name}")
        adapter = module.ADAPTER
    except (AttributeError, ModuleNotFoundError) as error:
        raise ConfigurationError(f"agent adapter is not available: {name}") from error
    if not isinstance(adapter, AgentAdapter) or adapter.name != name:
        raise ConfigurationError(f"invalid agent adapter: {name}")
    return adapter


__all__ = ["agent_adapter"]
