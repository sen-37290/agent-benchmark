"""Built-in benchmark modules."""

from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.exceptions import ConfigurationError


def benchmark_plugin(name: str) -> BenchmarkPlugin:
    if name == "swebench_verified":
        from agent_benchmark.benchmarks.swebench_verified import SwebenchVerified

        return SwebenchVerified()
    raise ConfigurationError(f"benchmark plugin is not registered: {name}")
