from __future__ import annotations

import json
import os
import random
from importlib.resources import files
from pathlib import Path

from agent_benchmark.exceptions import StageError

SAMPLING_SEED = 42
CATALOGS = {
    "cybergym_300": ("catalog-300.json", 300),
    "cybergym_smoke": ("catalog-smoke-10.json", 10),
}


def catalog(kind: str) -> dict[str, object]:
    try:
        filename, expected = CATALOGS[kind]
    except KeyError as error:
        raise StageError(f"unknown CyberGym catalog {kind!r}") from error
    path = files("agent_benchmark.benchmarks.cybergym").joinpath("assets", filename)
    payload = json.loads(path.read_text())
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != expected:
        raise StageError(f"CyberGym catalog {kind} must contain {expected} tasks")
    ids = [task.get("id") for task in tasks if isinstance(task, dict)]
    if (
        len(ids) != expected
        or len(ids) != len(set(ids))
        or not all(isinstance(i, str) for i in ids)
    ):
        raise StageError(f"CyberGym catalog {kind} contains invalid or duplicate task IDs")
    return payload


def create_pool(kind: str, output_path: Path, sampling: str | None, size: int | None) -> None:
    source = catalog(kind)
    tasks = sorted(source["tasks"], key=lambda task: task["id"])
    pin = os.environ.get("CYBERGYM_PIN_INSTANCE")
    if pin:
        # Escape hatch for single-task experiments: pin an exact instance id, bypassing
        # sampling. Only activates when the env var is set, so normal runs are unaffected.
        selected = [task for task in tasks if task["id"] == pin]
        if not selected:
            raise StageError(f"pinned instance {pin!r} is not in CyberGym catalog {kind}")
        strategy = "pinned"
    elif sampling is None and size is None:
        strategy = "full"
        selected = tasks
    else:
        if sampling != "random" or size is None:
            raise StageError("CyberGym sampling requires '--sampling random --size N'")
        if not 1 <= size <= len(tasks):
            raise StageError(f"CyberGym size must be between 1 and {len(tasks)}")
        strategy = sampling
        selected = sorted(
            random.Random(SAMPLING_SEED).sample(tasks, size), key=lambda task: task["id"]
        )
    payload = {
        "benchmark": "cybergym",
        "catalog": kind,
        "dataset_id": source["dataset_id"],
        "dataset_revision": source["dataset_revision"],
        "sampling": strategy,
        "seed": SAMPLING_SEED if strategy != "full" else None,
        "n": len(selected),
        "instance_ids": [task["id"] for task in selected],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n")
