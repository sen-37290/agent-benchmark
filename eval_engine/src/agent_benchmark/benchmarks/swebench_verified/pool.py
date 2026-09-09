from __future__ import annotations

import json
import os
import random
from collections import defaultdict
from pathlib import Path

from agent_benchmark.exceptions import StageError

DATASET_SIZE = 500
SAMPLING_SEED = 42
PREFIX_TO_DOMAIN = {
    "sympy": "Symbolic math",
    "sphinx-doc": "Dev tooling",
    "pytest-dev": "Dev tooling",
    "pylint-dev": "Dev tooling",
    "scikit-learn": "Scientific & data",
    "astropy": "Scientific & data",
    "pydata": "Scientific & data",
    "matplotlib": "Data-viz",
    "mwaskom": "Data-viz",
    "django": "Web & HTTP",
    "psf": "Web & HTTP",
    "pallets": "Web & HTTP",
}


def _pinned_ids() -> list[str] | None:
    """Instance ids from SWEBENCH_PIN_INSTANCES, or None when unset.

    Accepts a path to a JSON file ({"instance_ids": [...]}) or a comma-separated list. This is how
    a subset re-run selects exactly the tasks to redo -- e.g. the matplotlib instances a previous
    run lost to a container-start timeout -- instead of resampling the whole benchmark. Mirrors
    TERMINAL_BENCH_PIN_INSTANCES and CYBERGYM_PIN_INSTANCES.
    """
    raw = os.environ.get("SWEBENCH_PIN_INSTANCES")
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_file():
        data = json.loads(candidate.read_text())
        ids = data.get("instance_ids") if isinstance(data, dict) else data
    else:
        ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(ids, list) or not ids:
        raise StageError("SWEBENCH_PIN_INSTANCES resolved to an empty instance list")
    if len(ids) != len(set(ids)):
        raise StageError("SWEBENCH_PIN_INSTANCES contains duplicate instance ids")
    return [str(i) for i in ids]


def domain_of(instance_id: str) -> str:
    prefix = instance_id.split("__", 1)[0]
    try:
        return PREFIX_TO_DOMAIN[prefix]
    except KeyError as error:
        raise StageError(f"unknown SWE-bench repository prefix: {prefix}") from error


def sample(all_ids: list[str], strategy: str, size: int) -> list[str]:
    rng = random.Random(SAMPLING_SEED)
    if size == len(all_ids):
        return all_ids
    if strategy == "random":
        return sorted(rng.sample(all_ids, size))

    grouped: dict[str, list[str]] = defaultdict(list)
    for instance_id in all_ids:
        grouped[domain_of(instance_id)].append(instance_id)
    queues = {domain: rng.sample(items, len(items)) for domain, items in sorted(grouped.items())}
    selected: list[str] = []
    while len(selected) < size:
        for domain in sorted(queues):
            if queues[domain] and len(selected) < size:
                selected.append(queues[domain].pop())
    return sorted(selected)


def create_pool(output_path: Path, sampling: str | None, size: int | None) -> None:
    try:
        from datasets import load_dataset
    except ImportError as error:
        raise StageError("SWE-bench pool generation requires `uv sync --extra swebench`") from error

    dataset = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    all_ids = sorted(str(row["instance_id"]) for row in dataset)
    if len(all_ids) != DATASET_SIZE:
        raise StageError(f"expected {DATASET_SIZE} Verified tasks, got {len(all_ids)}")

    pinned = _pinned_ids()
    if pinned is not None:
        known = set(all_ids)
        unknown = [i for i in pinned if i not in known]
        if unknown:
            raise StageError(f"SWEBENCH_PIN_INSTANCES has unknown instance ids: {unknown[:5]}")
        strategy = "pinned"
        selected = sorted(pinned)
    elif sampling is None and size is None:
        strategy = "full"
        selected = all_ids
    else:
        assert sampling is not None and size is not None
        if sampling not in {"random", "domain"}:
            raise StageError("SWE-bench sampling must be 'random' or 'domain'")
        if not 1 <= size <= DATASET_SIZE:
            raise StageError(f"SWE-bench size must be between 1 and {DATASET_SIZE}")
        strategy = sampling
        selected = sample(all_ids, strategy, size)

    by_domain: dict[str, list[str]] = defaultdict(list)
    for instance_id in selected:
        by_domain[domain_of(instance_id)].append(instance_id)
    payload = {
        "benchmark": "swebench_verified",
        "dataset_id": "princeton-nlp/SWE-bench_Verified",
        "sampling": strategy,
        "seed": SAMPLING_SEED if strategy in {"random", "domain"} else None,
        "n": len(selected),
        "instance_ids": sorted(selected),
        "by_domain": dict(sorted(by_domain.items())),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n")
