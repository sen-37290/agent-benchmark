import json
from pathlib import Path

from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.run.result import TaskResult


def normalize_results(spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
    pool_path = run_dir / "inputs" / "pool.json"
    if pool_path.is_file():
        pool_data = json.loads(pool_path.read_text())
        trace_ids = pool_data.get("trace_ids", [])
    else:
        trace_ids = ["agent_shallow", "agent_deep", "agent_bursty", "agent_tool_heavy"]

    results: list[TaskResult] = []
    output_dir = run_dir / "artifacts" / "agent_perf"

    for trace_id in trace_ids:
        res_file = output_dir / f"{trace_id}_metrics.json"
        if res_file.is_file():
            try:
                data = json.loads(res_file.read_text())
                metrics = {
                    "resolved": data.get("goodput_passed", True),
                    "ttft_ms": data.get("ttft_ms", 0.0),
                    "itl_ms": data.get("itl_ms", 0.0),
                    "e2e_latency_ms": data.get("e2e_latency_ms", 0.0),
                    "goodput_rps": data.get("goodput_rps", 0.0),
                }
                results.append(
                    TaskResult(
                        run_id=spec.run_id,
                        task_id=trace_id,
                        status="completed",
                        metrics=metrics,
                        cost_usd=data.get("cost_usd", 0.0),
                        input_tokens=data.get("input_tokens", 0),
                        output_tokens=data.get("output_tokens", 0),
                    )
                )
            except Exception:
                results.append(
                    TaskResult(
                        run_id=spec.run_id,
                        task_id=trace_id,
                        status="error",
                        metrics={"resolved": False},
                    )
                )
        else:
            results.append(
                TaskResult(
                    run_id=spec.run_id,
                    task_id=trace_id,
                    status="missing",
                    metrics={"resolved": False},
                )
            )

    return results
