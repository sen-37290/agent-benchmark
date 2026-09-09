import json
from pathlib import Path

import pytest

TEST_POOL_IDS = ["django__django-13741", "pytest-dev__pytest-7571"]


@pytest.fixture
def pool_file(tmp_path: Path) -> Path:
    path = tmp_path / "generated-pool.json"
    path.write_text(json.dumps({"instance_ids": TEST_POOL_IDS}) + "\n")
    return path


class _HarborWithCurrentAgentOptions:
    """A Terminus 2 whose signature carries every agent option the engine relies on."""

    def __init__(
        self,
        logs_dir,
        model_name=None,
        *,
        stream=False,
        use_responses_api=False,
        **kwargs,
    ):
        pass


@pytest.fixture(autouse=True)
def installed_harbor_supports_current_agent_options(monkeypatch):
    """Pin what the Terminus 2 adapter sees when it introspects the installed Harbor.

    The adapter refuses to build an invocation whose agent kwargs the installed Harbor would
    silently drop -- Harbor's ``Terminus2.__init__`` ends in ``**kwargs``, so an unknown option
    is accepted and discarded, and a run configured for a transport it does not have would look
    completely normal afterwards. That check reads the Harbor that happens to be importable,
    which in a plain dev environment is the PyPI build rather than the git revision pinned for
    ``terminalbench``. Without this fixture the whole suite's result would depend on which
    extras were installed, and asserting anything about command construction would mean
    asserting something about the machine.

    Tests that exercise the refusal itself patch ``Terminus2`` again in their own body, which
    runs after this fixture and therefore wins.
    """
    try:
        import harbor.agents.terminus_2.terminus_2 as terminus_2_module
    except ImportError:
        # Harbor is an optional extra. Absent, the adapter's check no-ops and there is nothing
        # to stabilise.
        return
    monkeypatch.setattr(terminus_2_module, "Terminus2", _HarborWithCurrentAgentOptions)
