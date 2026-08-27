"""Evaluation harness adapters."""

from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.harnesses.base import HarnessAdapter


def harness_adapter(name: str) -> HarnessAdapter:
    if name == "harbor":
        from agent_benchmark.harnesses.harbor import HarborHarness

        return HarborHarness()
    if name == "aider-native":
        from agent_benchmark.harnesses.aider_native import AiderNativeHarness

        return AiderNativeHarness()
    if name == "mini-swe-agent-native":
        from agent_benchmark.harnesses.mini_swe_agent_native import MiniSweAgentNativeHarness

        return MiniSweAgentNativeHarness()
    if name == "cybergym-native":
        from agent_benchmark.harnesses.cybergym_native import CyberGymNativeHarness

        return CyberGymNativeHarness()
    raise ConfigurationError(f"harness adapter is not registered: {name}")
