"""Built-in benchmark modules."""

from agent_benchmark.benchmarks.base import BenchmarkPlugin
from agent_benchmark.exceptions import ConfigurationError


def benchmark_plugin(name: str) -> BenchmarkPlugin:
    if name == "swebench_verified":
        from agent_benchmark.benchmarks.swebench_verified import SwebenchVerified

        return SwebenchVerified()
    if name == "terminal_bench":
        from agent_benchmark.benchmarks.terminal_bench import TerminalBench

        return TerminalBench()
    if name == "aider_polyglot":
        from agent_benchmark.benchmarks.aider_polyglot import AiderPolyglot

        return AiderPolyglot()
    if name in {"cybergym_300", "cybergym_smoke"}:
        from agent_benchmark.benchmarks.cybergym import CyberGym

        return CyberGym(name)
    raise ConfigurationError(f"benchmark plugin is not registered: {name}")
