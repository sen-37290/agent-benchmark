import json
from collections import Counter
from pathlib import Path

import pytest

from agent_benchmark.benchmarks.terminal_bench.pool import DATASET_SIZE, create_pool
from agent_benchmark.exceptions import StageError


def read_pool(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def test_full_pool_is_default(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    create_pool(path, None, None)
    pool = read_pool(path)
    assert pool["n"] == DATASET_SIZE
    assert pool["sampling"] == "full"
    assert len(pool["instance_ids"]) == DATASET_SIZE
    assert sum(pool["by_category"].values()) == DATASET_SIZE


def test_random_pool_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    create_pool(first, "random", 7)
    create_pool(second, "random", 7)
    assert read_pool(first) == read_pool(second)


def test_category_pool_balances_available_categories(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    create_pool(path, "category", 16)
    counts = Counter(read_pool(path)["by_category"])
    assert len(counts) == 16
    assert set(counts.values()) == {1}


@pytest.mark.parametrize(("sampling", "size"), [("domain", 1), ("random", 90)])
def test_rejects_invalid_sampling(tmp_path: Path, sampling: str, size: int) -> None:
    with pytest.raises(StageError):
        create_pool(tmp_path / "pool.json", sampling, size)
