from pathlib import Path

from agent_benchmark.models import ResolvedSpec


def benchmark_dataset_dir(spec: ResolvedSpec, cache_root: Path) -> Path:
    return (
        cache_root
        / "datasets"
        / spec.benchmark.profile
        / (
            f"{spec.benchmark.sampling}-{spec.benchmark.sample_size}-"
            f"{spec.benchmark.pool_sha256[:12]}"
        )
    )
