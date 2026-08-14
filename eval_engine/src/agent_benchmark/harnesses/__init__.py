"""Execution harness adapters."""

from agent_benchmark.exceptions import ConfigurationError
from agent_benchmark.harnesses.base import HarnessAdapter


def harness_adapter(name: str) -> HarnessAdapter:
    if name == "harbor":
        from agent_benchmark.harnesses.harbor import HarborHarness

        return HarborHarness()
    if name == "mini-swe-agent-native":
        from agent_benchmark.harnesses.mini_swe_agent_native import MiniSweAgentNativeHarness

        return MiniSweAgentNativeHarness()
    if name == "aider-native":
        from agent_benchmark.harnesses.aider_native import AiderNativeHarness

        return AiderNativeHarness()
    if name == "agent-perf-native":
        from agent_benchmark.harnesses.agent_perf_native import AgentPerfNativeHarness

        return AgentPerfNativeHarness()
    if name == "arc-agi-3-native":
        from agent_benchmark.harnesses.arc_agi_3_native import ArcAgi3NativeHarness

        return ArcAgi3NativeHarness()
    raise ConfigurationError(f"harness adapter is not registered: {name}")
