"""Evaluation harness adapters."""

from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.harnesses.base import HarnessAdapter


def harness_adapter(name: str) -> HarnessAdapter:
    if name == "harbor":
        from agent_benchmark.harnesses.harbor import HarborHarness

        return HarborHarness()
    raise ConfigurationError(f"harness adapter is not registered: {name}")
