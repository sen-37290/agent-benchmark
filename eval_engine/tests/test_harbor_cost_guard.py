import asyncio
import sys
import types

import httpx

from agent_benchmark.harnesses.harbor_cost_guard import install_llm_retries


def test_llm_retry_wrapper_injects_long_read_timeout(monkeypatch) -> None:
    # Only the non-streaming transport needs a whole-response timeout; streaming sets a sharper
    # one of its own at the acompletion seam.
    monkeypatch.setenv("AGENT_BENCH_LLM_STREAMING", "0")
    seen = {}

    class FakeLiteLLM:
        async def call(self, *args, **kwargs):
            seen.update(kwargs)
            return "ok"

    module = types.ModuleType("harbor.llms.lite_llm")
    module.LiteLLM = FakeLiteLLM
    monkeypatch.setitem(sys.modules, "harbor", types.ModuleType("harbor"))
    monkeypatch.setitem(sys.modules, "harbor.llms", types.ModuleType("harbor.llms"))
    monkeypatch.setitem(sys.modules, "harbor.llms.lite_llm", module)

    assert install_llm_retries(2, 7200, 3600)
    assert asyncio.run(FakeLiteLLM().call("prompt")) == "ok"

    timeout = seen["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 3600
    assert timeout.connect == 60


def test_explicit_request_timeout_wins(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BENCH_LLM_STREAMING", "0")
    seen = {}

    class FakeLiteLLM:
        async def call(self, *args, **kwargs):
            seen.update(kwargs)
            return "ok"

    module = types.ModuleType("harbor.llms.lite_llm")
    module.LiteLLM = FakeLiteLLM
    monkeypatch.setitem(sys.modules, "harbor", types.ModuleType("harbor"))
    monkeypatch.setitem(sys.modules, "harbor.llms", types.ModuleType("harbor.llms"))
    monkeypatch.setitem(sys.modules, "harbor.llms.lite_llm", module)

    assert install_llm_retries(2, 7200, 3600)
    explicit = httpx.Timeout(90)
    assert asyncio.run(FakeLiteLLM().call("prompt", timeout=explicit)) == "ok"
    assert seen["timeout"] is explicit


def test_streaming_run_leaves_the_request_timeout_alone(monkeypatch) -> None:
    """A 3600s whole-response timeout is exactly what streaming replaces.

    Leaving it in kwargs would silently override the inter-byte idle timeout that makes a stalled
    request detectable, putting the run back in the state where a dead socket looks like a slow
    one for an hour.
    """
    monkeypatch.delenv("AGENT_BENCH_LLM_STREAMING", raising=False)
    seen = {}

    class FakeLiteLLM:
        async def call(self, *args, **kwargs):
            seen.update(kwargs)
            return "ok"

    module = types.ModuleType("harbor.llms.lite_llm")
    module.LiteLLM = FakeLiteLLM
    monkeypatch.setitem(sys.modules, "harbor", types.ModuleType("harbor"))
    monkeypatch.setitem(sys.modules, "harbor.llms", types.ModuleType("harbor.llms"))
    monkeypatch.setitem(sys.modules, "harbor.llms.lite_llm", module)

    assert install_llm_retries(2, 7200, 3600)
    assert asyncio.run(FakeLiteLLM().call("prompt")) == "ok"
    assert "timeout" not in seen
