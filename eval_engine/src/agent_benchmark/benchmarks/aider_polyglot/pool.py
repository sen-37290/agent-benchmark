from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from importlib.resources import files
from pathlib import Path

from agent_benchmark.exceptions import StageError

DATASET_SIZE = 225
SAMPLING_SEED = 42
LANGUAGE_COUNTS = {
    "cpp": 26,
    "go": 39,
    "java": 47,
    "javascript": 49,
    "python": 34,
    "rust": 30,
}


def catalog() -> dict[str, object]:
    path = files("agent_benchmark.benchmarks.aider_polyglot").joinpath("assets/catalog.json")
    payload = json.loads(path.read_text())
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != DATASET_SIZE:
        raise StageError(f"Aider Polyglot catalog must contain {DATASET_SIZE} tasks")
    ids = [task.get("id") for task in tasks if isinstance(task, dict)]
    if len(ids) != DATASET_SIZE or len(ids) != len(set(ids)):
        raise StageError("Aider Polyglot catalog contains invalid or duplicate task IDs")
    counts = Counter(str(task["language"]) for task in tasks)
    if dict(sorted(counts.items())) != LANGUAGE_COUNTS:
        raise StageError(f"unexpected Aider Polyglot language distribution: {dict(counts)}")
    return payload


def sample(tasks: list[dict[str, str]], strategy: str, size: int) -> list[dict[str, str]]:
    rng = random.Random(SAMPLING_SEED)
    if size == len(tasks):
        return tasks
    if strategy == "random":
        return sorted(rng.sample(tasks, size), key=lambda task: task["id"])

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for task in tasks:
        grouped[task["language"]].append(task)
    queues = {
        language: rng.sample(items, len(items)) for language, items in sorted(grouped.items())
    }
    selected: list[dict[str, str]] = []
    while len(selected) < size:
        for language in sorted(queues):
            if queues[language] and len(selected) < size:
                selected.append(queues[language].pop())
    return sorted(selected, key=lambda task: task["id"])


def create_pool(output_path: Path, sampling: str | None, size: int | None) -> None:
    source = catalog()
    tasks = sorted(source["tasks"], key=lambda task: task["id"])
    if sampling is None and size is None:
        strategy = "full"
        selected = tasks
    else:
        assert sampling is not None and size is not None
        if sampling not in {"random", "language"}:
            raise StageError("Aider Polyglot sampling must be 'random' or 'language'")
        if not 1 <= size <= DATASET_SIZE:
            raise StageError(f"Aider Polyglot size must be between 1 and {DATASET_SIZE}")
        strategy = sampling
        selected = sample(tasks, strategy, size)

    payload = {
        "benchmark": "aider_polyglot",
        "dataset_id": source["dataset_id"],
        "dataset_revision": source["dataset_revision"],
        "sampling": strategy,
        "seed": SAMPLING_SEED if strategy != "full" else None,
        "n": len(selected),
        "instance_ids": [task["id"] for task in selected],
        "task_digests": {task["id"]: task["digest"] for task in selected},
        "by_language": dict(sorted(Counter(task["language"] for task in selected).items())),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n")
