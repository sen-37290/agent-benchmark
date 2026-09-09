"""Pre-pull the per-instance Docker images a SWE-bench run needs.

WHY THIS EXISTS. mini-swe-agent starts each task with `docker run <image>`, which pulls the image
implicitly, and it wraps that call in `subprocess.run(..., timeout=pull_timeout)` with
`pull_timeout` defaulting to 120 seconds (`minisweagent/environments/docker.py`). At 30 workers
the scheduler launches ~30 first-touch pulls at once; the matplotlib images are the largest in
the dataset and a wave of them never completes inside 120s, so every task in the wave dies with
`subprocess.TimeoutExpired` before the model is ever called. Retrying replays the identical
herd, so it never converges: the 20260903 runs lost 18 matplotlib tasks each on gpt-5.6-terra
and gpt-5.6-luna, and 16 on fable-5-1, at a cost of $0.00 and with no trajectory. The only run
that escaped (gpt-5.6-sol) did so because its VM happened to hold a warm image cache.

Pulling the same bytes here, before any task starts and a few at a time, removes the herd. Once
the image is local, `docker run` returns in well under a second and the timeout cannot fire.

ONE PULL SERVES BOTH PHASES. The official grader resolves the very same reference --
`swebench/sweb.eval.x86_64.<instance id>:latest` via `TestSpec.instance_image_key` with the
default `--namespace swebench` -- and pulls it only when `client.images.get()` misses
(`swebench/harness/docker_build.py`). So warming the cache for the agent phase also warms it for
grading; there is no second list of images to prepare.

This is idempotent and cheap on a warm VM: it lists local images once and pulls only what is
missing, so a second run over the same pool does no work at all.
"""

from __future__ import annotations

import concurrent.futures
import shutil
import subprocess
from pathlib import Path

# Both mini-swe-agent and the official grader build this reference; keep the two in sync by
# construction rather than by hope. mini-swe-agent lowercases the whole string after swapping the
# `__` separator, and the grader lowercases the instance id before swapping -- same result.
IMAGE_REGISTRY = "docker.io/swebench"
IMAGE_TAG = "latest"

# Generous by design: this bound exists to stop a wedged pull, not to ration a slow one. The
# 120-second default it replaces is exactly what lost the matplotlib tasks.
DEFAULT_PULL_TIMEOUT_SECONDS = 1800
# Deliberately far below the worker count. The whole failure being fixed here is too many
# concurrent pulls saturating disk and network, so the fix must not recreate it at a smaller
# scale. Four keeps the link busy without starving any single pull.
DEFAULT_PULL_WORKERS = 4
DEFAULT_PULL_ATTEMPTS = 3


def image_name(instance_id: str) -> str:
    """The container image for one SWE-bench instance.

    Mirrors `minisweagent.run.benchmarks.swebench.get_swebench_docker_image_name`: Docker
    forbids a double underscore in a repository name, so the instance id's `__` separator is
    replaced with the `_1776_` magic token used by the published images.
    """
    slug = instance_id.replace("__", "_1776_")
    return f"{IMAGE_REGISTRY}/sweb.eval.x86_64.{slug}:{IMAGE_TAG}".lower()


def _local_images() -> set[str]:
    """Exactly the image references `docker images` reports on this host."""
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _is_present(name: str, present: set[str]) -> bool:
    """Whether `name` is already local, in either spelling.

    `docker images` prints `swebench/sweb.eval...` for something pulled as
    `docker.io/swebench/sweb.eval...`: the default registry is normalised away in the listing but
    not in the reference we pull by. Comparing both spellings here, rather than synthesising them
    into the inventory, keeps `_local_images` an honest report of what docker said.
    """
    short = name.removeprefix("docker.io/")
    return name in present or short in present or f"docker.io/{short}" in present


def _pull(name: str, timeout: int, attempts: int) -> str | None:
    """Pull one image, retrying a transient failure. Returns an error string, or None on success."""
    last = "no attempt was made"
    for _ in range(attempts):
        try:
            result = subprocess.run(
                ["docker", "pull", "--quiet", name],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout}s"
            continue
        if result.returncode == 0:
            return None
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        last = lines[-1] if lines else "unknown error"
        # A missing repository or tag will never appear on a retry; anything else might.
        if "not found" in last or "manifest unknown" in last:
            break
    return last


def prepull(
    instance_ids: list[str],
    log_path: Path,
    *,
    timeout: int = DEFAULT_PULL_TIMEOUT_SECONDS,
    workers: int = DEFAULT_PULL_WORKERS,
    attempts: int = DEFAULT_PULL_ATTEMPTS,
) -> list[str]:
    """Ensure every instance image is local; return the instance ids that could not be pulled.

    Failures are returned to the caller. The benchmark prepare stage treats a non-empty result as
    an infrastructure failure and retries the stage, so no model request starts while an image is
    missing. Successful pulls survive that retry and are skipped by the next inventory pass.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    wanted = {instance_id: image_name(instance_id) for instance_id in sorted(set(instance_ids))}
    if shutil.which("docker") is None:
        with log_path.open("a", encoding="utf-8") as log:
            log.write("docker is not on PATH; skipping image pre-pull\n")
        return sorted(wanted)
    try:
        present = _local_images()
    except (OSError, subprocess.SubprocessError) as error:
        # Not fatal: without an inventory the run simply falls back to pulling on first use,
        # which is the behaviour that existed before this step.
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"could not list local images ({error}); skipping image pre-pull\n")
        return sorted(wanted)
    missing = {
        instance_id: name for instance_id, name in wanted.items() if not _is_present(name, present)
    }
    with log_path.open("a", encoding="utf-8") as log:
        log.write(
            f"pre-pulling SWE-bench images: {len(wanted)} in pool, {len(missing)} missing, "
            f"{workers} at a time (timeout {timeout}s, {attempts} attempts)\n"
        )
        log.flush()
        if not missing:
            log.write("all images already present; nothing to pull\n")
            return []

        failed: list[str] = []
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_pull, name, timeout, attempts): instance_id
                for instance_id, name in missing.items()
            }
            for future in concurrent.futures.as_completed(futures):
                instance_id = futures[future]
                done += 1
                try:
                    error = future.result()
                except Exception as exc:  # noqa: BLE001 - reported, never fatal
                    error = str(exc)
                if error is None:
                    log.write(f"[{done}/{len(missing)}] pulled {instance_id}\n")
                else:
                    failed.append(instance_id)
                    log.write(f"[{done}/{len(missing)}] FAILED {instance_id}: {error}\n")
                log.flush()
        # A successful pull exit is not sufficient evidence for the launch gate. Refresh Docker's
        # inventory and verify that every requested tag is now addressable before generation.
        try:
            refreshed = _local_images()
        except (OSError, subprocess.SubprocessError) as error:
            log.write(f"could not verify pulled images ({error})\n")
            return sorted(wanted)
        failed = sorted(
            set(failed)
            | {
                instance_id
                for instance_id, name in wanted.items()
                if not _is_present(name, refreshed)
            }
        )
        if failed:
            log.write(
                f"{len(failed)} image(s) remain unavailable after pre-pull: "
                f"{', '.join(sorted(failed))}\n"
            )
        return sorted(failed)
