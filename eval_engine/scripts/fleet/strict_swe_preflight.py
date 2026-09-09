#!/usr/bin/env python3
"""Require every SWE-bench task/grader image before a paid experiment starts."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import time
from pathlib import Path

from agent_benchmark.benchmarks.swebench_verified.images import image_name, prepull
from agent_benchmark.benchmarks.swebench_verified.pool import create_pool
from agent_benchmark.run.retry import retry_delay


def _inspect(name: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", name],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def missing_images(instance_ids: list[str], workers: int = 8) -> list[str]:
    """Return task IDs whose exact mini-swe-agent/grader image is not inspectable."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        checks = {
            pool.submit(_inspect, image_name(instance_id)): instance_id
            for instance_id in instance_ids
        }
        return sorted(instance_id for future, instance_id in checks.items() if not future.result())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    if shutil.which("docker") is None:
        parser.error("docker is required for the strict SWE image gate")
    if args.retries < 0:
        parser.error("--retries must be non-negative")

    args.pool.parent.mkdir(parents=True, exist_ok=True)
    create_pool(args.pool, sampling=None, size=None)
    pool_data = json.loads(args.pool.read_text())
    instance_ids = pool_data.get("instance_ids")
    if not isinstance(instance_ids, list) or not instance_ids:
        parser.error("generated SWE pool has no instance_ids")

    pull_workers = max(1, min(args.workers, 4))
    attempts = args.retries + 1
    for attempt in range(1, attempts + 1):
        with args.log.open("a", encoding="utf-8") as log:
            log.write(
                f"strict image gate round {attempt}/{attempts}: "
                f"{len(instance_ids)} images, {pull_workers} concurrent pulls\n"
            )
        # One pull attempt per round. Successful images remain cached and the next round skips
        # them, giving exactly one initial attempt plus the requested number of retries.
        prepull(
            instance_ids,
            args.log,
            workers=pull_workers,
            attempts=1,
        )
        missing = missing_images(instance_ids, workers=pull_workers)
        with args.log.open("a", encoding="utf-8") as log:
            log.write(
                f"strict image verification: {len(instance_ids) - len(missing)}/"
                f"{len(instance_ids)} inspectable\n"
            )
            if missing:
                log.write(f"still missing: {', '.join(missing)}\n")
        if not missing:
            print(f"verified {len(instance_ids)} SWE generation/grading images")
            return 0
        if attempt < attempts:
            time.sleep(retry_delay(attempt))

    print(
        f"FATAL: {len(missing)}/{len(instance_ids)} SWE images unavailable after "
        f"{attempts} attempts; no model request was made",
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
