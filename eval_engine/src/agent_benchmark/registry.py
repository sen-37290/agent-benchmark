from __future__ import annotations

from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.errors import ConfigurationError
from agent_benchmark.harnesses.base import HarnessAdapter


def benchmark_plugin(name: str) -> BenchmarkPlugin:
    if name == "swebench_verified":
        from agent_benchmark.benchmarks.swebench_verified import SwebenchVerified

        return SwebenchVerified()
    raise ConfigurationError(f"benchmark plugin is not registered: {name}")


def harness_adapter(name: str) -> HarnessAdapter:
    if name == "harbor":
        from agent_benchmark.harnesses.harbor import HarborHarness

        return HarborHarness()
    raise ConfigurationError(f"harness adapter is not registered: {name}")
