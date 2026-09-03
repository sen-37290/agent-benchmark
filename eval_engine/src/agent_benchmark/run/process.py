from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agent_benchmark.exceptions import StageError
from agent_benchmark.run.stop import REASON_COST_CAP, request_stop


def collected_cost(job_dir: Path) -> float:
    """Best-effort total of completed Harbor trial costs."""
    total = 0.0
    paths = job_dir.glob("**/result.json") if job_dir.exists() else ()
    for path in paths:
        if not ((path.parent / "agent").is_dir() or (path.parent / "verifier").is_dir()):
            continue
        try:
            result = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        contexts = []
        if result.get("agent_result"):
            contexts.append(result["agent_result"])
        else:
            contexts.extend(
                step["agent_result"]
                for step in (result.get("step_results") or [])
                if step.get("agent_result")
            )
        total += sum(float(context.get("cost_usd") or 0.0) for context in contexts)
    return total


def measured_cost(cost_reader: Callable[[], float] | None, job_dir: Path | None) -> float:
    if cost_reader is not None:
        return cost_reader()
    assert job_dir is not None
    return collected_cost(job_dir)


def run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    env: Mapping[str, str] | None = None,
    redact_values: Sequence[str] = (),
    budget_job_dir: Path | None = None,
    cost_reader: Callable[[], float] | None = None,
    budget_usd: float | None = None,
    poll_seconds: float = 5.0,
    stop_dir: Path | None = None,
) -> None:
    """Run a command and stream redacted output to both the terminal and a durable log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    with log_path.open("a") as log:
        display = " ".join(command)
        for value in redact_values:
            if value:
                display = display.replace(value, "<redacted>")
        heading = f"$ {display}\n"
        log.write(heading)
        log.flush()
        print(heading, end="", flush=True)
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        assert process.stdout is not None
        output: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            for line in process.stdout:
                output.put(line)
            output.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        budget_tripped = False
        output_finished = False
        next_budget_check = time.monotonic()
        while process.poll() is None or not output_finished:
            try:
                line = output.get(timeout=0.2)
            except queue.Empty:
                line = ""
            if line is None:
                output_finished = True
            elif line:
                for value in redact_values:
                    if value:
                        line = line.replace(value, "<redacted>")
                log.write(line)
                log.flush()
                print(line, end="", flush=True)

            now = time.monotonic()
            if (
                process.poll() is None
                and now >= next_budget_check
                and (budget_job_dir is not None or cost_reader is not None)
                and budget_usd is not None
            ):
                spent = measured_cost(cost_reader, budget_job_dir)
                if spent >= budget_usd:
                    budget_tripped = True
                    message = f"budget tripped: measured ${spent:.4f} >= ${budget_usd:.4f}\n"
                    log.write(message)
                    log.flush()
                    print(message, end="", flush=True)
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                next_budget_check = now + poll_seconds
        reader.join(timeout=1)
        returncode = process.wait()
    if budget_tripped:
        if stop_dir is None:
            raise StageError(f"total generation budget reached (${budget_usd:.2f})")
        # Reaching the experiment's cost cap is a planned stop, not a failure. Record it and
        # return normally so the pipeline still grades and reports the tasks that finished --
        # raising here used to discard the whole run's report.
        request_stop(
            stop_dir,
            REASON_COST_CAP,
            f"measured ${measured_cost(cost_reader, budget_job_dir):.4f} "
            f"reached the ${budget_usd:.2f} cap",
        )
        return
    if returncode:
        raise StageError(f"command failed with exit code {returncode}; see {log_path}")
