from __future__ import annotations

import json
import os
import random
from collections import Counter, defaultdict
from importlib.resources import files
from pathlib import Path

from agent_benchmark.exceptions import StageError

DATASET_SIZE = 89
SAMPLING_SEED = 42


def _pinned_ids() -> list[str] | None:
    """Task ids from TERMINAL_BENCH_PIN_INSTANCES, or None when unset.

    Accepts a path to a JSON file ({"instance_ids": [...]}) or a comma-separated list. This is how
    a subset re-run selects exactly the tasks to redo (e.g. the ones a previous run timed out on)
    instead of resampling the whole benchmark. Mirrors CYBERGYM_PIN_INSTANCES.
    """
    raw = os.environ.get("TERMINAL_BENCH_PIN_INSTANCES")
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_file():
        data = json.loads(candidate.read_text())
        ids = data.get("instance_ids") if isinstance(data, dict) else data
    else:
        ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(ids, list) or not ids:
        raise StageError("TERMINAL_BENCH_PIN_INSTANCES resolved to an empty instance list")
    if len(ids) != len(set(ids)):
        raise StageError("TERMINAL_BENCH_PIN_INSTANCES contains duplicate instance ids")
    return [str(i) for i in ids]


def catalog() -> dict[str, object]:
    path = files("agent_benchmark.benchmarks.terminal_bench").joinpath("assets/catalog.json")
    payload = json.loads(path.read_text())
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != DATASET_SIZE:
        raise StageError(f"Terminal-Bench catalog must contain {DATASET_SIZE} tasks")
    return payload


def sample(tasks: list[dict[str, str]], strategy: str, size: int) -> list[dict[str, str]]:
    rng = random.Random(SAMPLING_SEED)
    if size == len(tasks):
        return tasks
    if strategy == "random":
        return sorted(rng.sample(tasks, size), key=lambda task: task["id"])

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for task in tasks:
        grouped[task["category"]].append(task)
    queues = {
        category: rng.sample(items, len(items)) for category, items in sorted(grouped.items())
    }
    selected: list[dict[str, str]] = []
    while len(selected) < size:
        for category in sorted(queues):
            if queues[category] and len(selected) < size:
                selected.append(queues[category].pop())
    return sorted(selected, key=lambda task: task["id"])


def create_pool(output_path: Path, sampling: str | None, size: int | None) -> None:
    source = catalog()
    tasks = sorted(source["tasks"], key=lambda task: task["id"])
    pinned = _pinned_ids()
    if pinned is not None:
        by_id = {task["id"]: task for task in tasks}
        unknown = [i for i in pinned if i not in by_id]
        if unknown:
            raise StageError(f"TERMINAL_BENCH_PIN_INSTANCES has unknown task ids: {unknown[:5]}")
        strategy = "pinned"
        selected = sorted((by_id[i] for i in pinned), key=lambda task: task["id"])
    elif sampling is None and size is None:
        strategy = "full"
        selected = tasks
    else:
        assert sampling is not None and size is not None
        if sampling not in {"random", "category"}:
            raise StageError("Terminal-Bench sampling must be 'random' or 'category'")
        if not 1 <= size <= DATASET_SIZE:
            raise StageError(f"Terminal-Bench size must be between 1 and {DATASET_SIZE}")
        strategy = sampling
        selected = sample(tasks, strategy, size)

    payload = {
        "benchmark": "terminal_bench",
        "dataset_id": source["dataset_id"],
        "dataset_digest": source["dataset_digest"],
        "sampling": strategy,
        "seed": SAMPLING_SEED if strategy in {"random", "category"} else None,
        "n": len(selected),
        "instance_ids": [task["id"] for task in selected],
        "task_digests": {task["id"]: task["digest"] for task in selected},
        "by_category": dict(sorted(Counter(task["category"] for task in selected).items())),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n")
