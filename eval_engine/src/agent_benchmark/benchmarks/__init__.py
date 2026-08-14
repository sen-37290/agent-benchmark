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
    if name == "agent_perf":
        from agent_benchmark.benchmarks.agent_perf import AgentPerf

        return AgentPerf()
    if name == "arc_agi_3":
        from agent_benchmark.benchmarks.arc_agi_3 import ArcAgi3

        return ArcAgi3()
    raise ConfigurationError(f"benchmark plugin is not registered: {name}")
