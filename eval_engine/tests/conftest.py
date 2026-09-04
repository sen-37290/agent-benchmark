import json
from pathlib import Path

import pytest

TEST_POOL_IDS = ["django__django-13741", "pytest-dev__pytest-7571"]


@pytest.fixture
def pool_file(tmp_path: Path) -> Path:
    path = tmp_path / "generated-pool.json"
    path.write_text(json.dumps({"instance_ids": TEST_POOL_IDS}) + "\n")
    return path
