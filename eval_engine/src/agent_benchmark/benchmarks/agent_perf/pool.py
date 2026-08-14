import json
import random
from pathlib import Path

from agent_benchmark.benchmarks.agent_perf.assets.catalog import CATALOG
from agent_benchmark.exceptions import ConfigurationError


def generate_pool(output_path: Path, sampling: str | None, size: int | None) -> None:
    all_ids = [item["id"] for item in CATALOG]

    if sampling is None and size is None:
        selected_ids = all_ids
    elif sampling in (None, "random"):
        count = size if size is not None else len(all_ids)
        if count < 1 or count > len(all_ids):
            raise ConfigurationError(f"size must be between 1 and {len(all_ids)}")
        rng = random.Random(42)
        shuffled = list(all_ids)
        rng.shuffle(shuffled)
        selected_ids = sorted(shuffled[:count])
    else:
        raise ConfigurationError(f"unsupported sampling strategy for agent-perf: {sampling}")

    pool_data = {
        "benchmark": "agent-perf",
        "instance_ids": selected_ids,
        "trace_ids": selected_ids,
        "count": len(selected_ids),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(pool_data, indent=2))
