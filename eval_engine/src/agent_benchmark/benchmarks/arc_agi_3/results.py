import json
from pathlib import Path

from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.run.result import TaskResult


def normalize_results(spec: ResolvedSpec, run_dir: Path) -> list[TaskResult]:
    pool_path = run_dir / "inputs" / "pool.json"
    if pool_path.is_file():
        pool_data = json.loads(pool_path.read_text())
        env_ids = pool_data.get("environment_ids", [])
    else:
        env_ids = [
            "arc_env_001_exploration",
            "arc_env_002_modeling",
            "arc_env_003_goal_setting",
            "arc_env_004_continual_learning",
        ]

    results: list[TaskResult] = []
    output_dir = run_dir / "artifacts" / "arc_agi_3"

    for env_id in env_ids:
        res_file = output_dir / f"{env_id}_result.json"
        if res_file.is_file():
            try:
                data = json.loads(res_file.read_text())
                solved = data.get("solved", False)
                metrics = {
                    "resolved": solved,
                    "score": data.get("score", 0.0),
                    "steps": data.get("steps", 0),
                    "levels_completed": data.get("levels_completed", 0),
                }
                results.append(
                    TaskResult(
                        run_id=spec.run_id,
                        task_id=env_id,
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
                        task_id=env_id,
                        status="error",
                        metrics={"resolved": False},
                    )
                )
        else:
            results.append(
                TaskResult(
                    run_id=spec.run_id,
                    task_id=env_id,
                    status="missing",
                    metrics={"resolved": False},
                )
            )

    return results
