import json
from collections import Counter
from pathlib import Path

import pytest

from agent_benchmark.benchmarks.aider_polyglot.pool import (
    DATASET_SIZE,
    LANGUAGE_COUNTS,
    catalog,
    create_pool,
)
from agent_benchmark.exceptions import StageError


def read_pool(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def test_catalog_has_pinned_full_distribution() -> None:
    source = catalog()
    tasks = source["tasks"]

    assert source["dataset_revision"] == "7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f"
    assert len(tasks) == DATASET_SIZE
    assert len({task["id"] for task in tasks}) == DATASET_SIZE
    assert Counter(task["language"] for task in tasks) == LANGUAGE_COUNTS
    assert all(task["path"].endswith(task["id"].split("/", 1)[1]) for task in tasks)


def test_full_pool_is_default(tmp_path: Path) -> None:
    destination = tmp_path / "pool.json"
    create_pool(destination, None, None)
    pool = read_pool(destination)

    assert pool["sampling"] == "full"
    assert pool["n"] == DATASET_SIZE
    assert pool["by_language"] == LANGUAGE_COUNTS


@pytest.mark.parametrize("strategy", ["random", "language"])
def test_sampling_is_deterministic(tmp_path: Path, strategy: str) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    create_pool(first, strategy, 13)
    create_pool(second, strategy, 13)
    assert read_pool(first) == read_pool(second)


def test_language_sampling_balances_languages(tmp_path: Path) -> None:
    destination = tmp_path / "pool.json"
    create_pool(destination, "language", 12)
    assert set(read_pool(destination)["by_language"].values()) == {2}


@pytest.mark.parametrize(("strategy", "size"), [("category", 2), ("random", 226)])
def test_invalid_sampling_is_rejected(tmp_path: Path, strategy: str, size: int) -> None:
    with pytest.raises(StageError):
        create_pool(tmp_path / "pool.json", strategy, size)
