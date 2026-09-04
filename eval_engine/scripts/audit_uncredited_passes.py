#!/usr/bin/env python3
"""Find Terminal-Bench trials whose verifier passed but whose score was thrown away.

Harbor grades the container state after the agent phase ends, so a trial can hold a genuine
verifier reward of 1 *and* an ``exception_info`` -- most often an ``AgentTimeoutError``, where the
agent ran past its deadline having already produced a correct artifact. Normalization used to
force the reward to 0 for any trial with an exception, which silently converted proven passes into
failures. ``benchmarks/terminal_bench/results.py`` now trusts the verifier; this script reports
what that change is worth on runs that were normalized before the fix.

It reads only ``result.json`` files, so it works against an archived run directory with no engine
checkout and mutates nothing.

    python3 audit_uncredited_passes.py <run-dir> [<run-dir> ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def trials(run_dir: Path):
    for result_path in sorted(run_dir.glob("artifacts/harbor_jobs/*/*/result.json")):
        try:
            yield result_path, json.loads(result_path.read_text())
        except (OSError, ValueError):
            continue


def audit(run_dir: Path) -> dict[str, object]:
    uncredited, scored, errored, total = [], 0, 0, 0
    for _, raw in trials(run_dir):
        total += 1
        name = str(raw.get("task_name", "?")).rsplit("/", 1)[-1]
        exception = raw.get("exception_info")
        error_type = exception.get("exception_type") if isinstance(exception, dict) else None
        verifier = raw.get("verifier_result")
        rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
        reward = rewards.get("reward") if isinstance(rewards, dict) else None
        if isinstance(reward, bool) or not isinstance(reward, int | float):
            reward = None
        if error_type:
            errored += 1
        if reward is not None:
            scored += 1
        # The defect: a verified pass discarded because the agent phase raised.
        if error_type and reward is not None and reward > 0:
            uncredited.append((name, error_type, reward))
    return {
        "run": run_dir.name,
        "trials": total,
        "verifier_scored": scored,
        "agent_phase_errors": errored,
        "uncredited": uncredited,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 64
    grand_total = 0
    for raw_path in argv:
        run_dir = Path(raw_path).expanduser()
        report = audit(run_dir)
        uncredited = report["uncredited"]
        grand_total += len(uncredited)
        print(f"\n{run_dir}")
        print(
            f"  {report['trials']} trials, {report['verifier_scored']} verifier-scored, "
            f"{report['agent_phase_errors']} with an agent-phase error"
        )
        if not uncredited:
            print("  no uncredited passes")
            continue
        print(f"  UNCREDITED PASSES: {len(uncredited)}")
        for name, error_type, reward in sorted(uncredited):
            print(f"    {name:34} {error_type:22} reward={reward}")
    print(f"\ntotal uncredited passes across {len(argv)} run(s): {grand_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
