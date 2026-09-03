#!/usr/bin/env python3
"""Monitor and control the ten concurrent experiments from a laptop.

Each VM is an execution worker, not the source of truth. This tool polls every VM in parallel for
the small status file its controller keeps fresh, and caches each poll locally, so a VM that has
crashed or been shut down still shows its last known state with an age marker rather than
vanishing from the table.

    ./fleet.py status                 one-shot table
    ./fleet.py watch [--interval 60]  refreshing table
    ./fleet.py logs <label> [-f]      the controller's journal
    ./fleet.py ssh <label>            an interactive shell on the right VM
    ./fleet.py stop <label>           drain cleanly, then grade and report
    ./fleet.py labels                 list experiment labels
    ./fleet.py env <label>            shell assignments for one experiment (used by deploy)

No dependency beyond the standard library and PyYAML, so it runs from a bare checkout.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
EXPERIMENTS_FILE = HERE / "experiments.yaml"
CACHE_FILE = HERE / ".fleet-cache" / "last_snapshot.json"
SSH_OPTS = [
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "BatchMode=yes",
]

# Dependency extras per benchmark, mirroring each profile's `dependency_extra` setting.
DEPENDENCY_EXTRA = {
    "swebench-verified": "swebench",
    "terminal-bench-2.1": "terminalbench",
    "cybergym-300-level1": "cybergym",
    "cybergym-smoke-10-level1": "cybergym",
}


@dataclass
class Experiment:
    label: str
    vm: str
    benchmark: str
    model: str
    provider: str
    api_key_from: str
    experiment_cap_usd: float
    workers: int
    per_task_cap_usd: float | None
    project: str
    zone: str
    remote_root: str
    ssh_user: str

    @property
    def dependency_extra(self) -> str:
        return DEPENDENCY_EXTRA.get(self.benchmark, "")


def load_experiments() -> list[Experiment]:
    raw = yaml.safe_load(EXPERIMENTS_FILE.read_text())
    defaults = raw.get("defaults", {})
    experiments = []
    for row in raw["experiments"]:
        experiments.append(
            Experiment(
                label=row["label"],
                vm=row["vm"],
                benchmark=row["benchmark"],
                model=row["model"],
                provider=row["provider"],
                api_key_from=row["api_key_from"],
                experiment_cap_usd=float(row["experiment_cap_usd"]),
                workers=int(row["workers"]),
                per_task_cap_usd=(
                    float(row["per_task_cap_usd"]) if row.get("per_task_cap_usd") else None
                ),
                project=defaults["project"],
                zone=defaults["zone"],
                remote_root=defaults["remote_root"],
                ssh_user=defaults["ssh_user"],
            )
        )
    return experiments


def find(label: str) -> Experiment:
    for experiment in load_experiments():
        if experiment.label == label:
            return experiment
    sys.exit(f"unknown label: {label}\nknown: {', '.join(e.label for e in load_experiments())}")


# --------------------------------------------------------------------------- VM addressing


_IP_CACHE: dict[str, str] = {}


def vm_ip(experiment: Experiment) -> str:
    """External IP of the experiment's VM, resolved once per process via gcloud."""
    if experiment.vm in _IP_CACHE:
        return _IP_CACHE[experiment.vm]
    result = subprocess.run(
        [
            "gcloud",
            "compute",
            "instances",
            "describe",
            experiment.vm,
            f"--project={experiment.project}",
            f"--zone={experiment.zone}",
            "--format=value(networkInterfaces[0].accessConfigs[0].natIP)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    ip = result.stdout.strip()
    if not ip:
        raise RuntimeError(f"no external IP for {experiment.vm}: {result.stderr.strip()}")
    _IP_CACHE[experiment.vm] = ip
    return ip


def ssh_command(experiment: Experiment, remote: str | None = None) -> list[str]:
    key = os.path.expanduser("~/.ssh/google_compute_engine")
    command = ["ssh", *SSH_OPTS]
    if os.path.exists(key):
        command += ["-i", key]
    command.append(f"{experiment.ssh_user}@{vm_ip(experiment)}")
    if remote:
        command.append(remote)
    return command


# --------------------------------------------------------------------------- polling


def poll(experiment: Experiment) -> dict[str, Any]:
    """Fetch one experiment's status file. Never raises: unreachable is a reportable state."""
    status_path = f"{experiment.remote_root}/.fleet/{experiment.label}.status.json"
    unit = f"agent-bench@{experiment.label}"
    remote = (
        f"cat {status_path} 2>/dev/null || true; "
        f"echo '---UNIT---'; systemctl is-active {unit} 2>/dev/null || true"
    )
    try:
        result = subprocess.run(
            ssh_command(experiment, remote), capture_output=True, text=True, timeout=45
        )
    except (subprocess.TimeoutExpired, RuntimeError, OSError) as error:
        return {"label": experiment.label, "reachable": False, "note": type(error).__name__}

    payload, _, unit_state = result.stdout.partition("---UNIT---")
    record: dict[str, Any] = {
        "label": experiment.label,
        "reachable": result.returncode == 0,
        "unit": unit_state.strip() or "unknown",
        "polled_at": time.time(),
    }
    payload = payload.strip()
    if payload:
        try:
            record["snapshot"] = json.loads(payload)
        except ValueError:
            record["note"] = "unreadable status file"
    return record


def poll_all(experiments: list[Experiment]) -> dict[str, dict[str, Any]]:
    with futures.ThreadPoolExecutor(max_workers=len(experiments)) as pool:
        results = list(pool.map(poll, experiments))
    return {record["label"]: record for record in results}


def merge_with_cache(fresh: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fall back to the last known state for any VM that could not be reached.

    This is what keeps the table honest when a VM crashes: the row stays, marked stale, instead of
    silently disappearing or reading as zero progress.
    """
    cached: dict[str, dict[str, Any]] = {}
    if CACHE_FILE.is_file():
        try:
            cached = json.loads(CACHE_FILE.read_text())
        except (OSError, ValueError):
            cached = {}

    merged: dict[str, dict[str, Any]] = {}
    for label, record in fresh.items():
        if record.get("snapshot") is None and label in cached and cached[label].get("snapshot"):
            stale = dict(cached[label])
            stale["stale"] = True
            stale["reachable"] = record.get("reachable", False)
            stale["unit"] = record.get("unit", stale.get("unit", "unknown"))
            merged[label] = stale
        else:
            merged[label] = record

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    keep = {label: r for label, r in merged.items() if r.get("snapshot")}
    CACHE_FILE.write_text(json.dumps(keep, indent=2))
    return merged


# --------------------------------------------------------------------------- rendering


def _status_icon(record: dict[str, Any]) -> str:
    snapshot = record.get("snapshot") or {}
    if record.get("stale"):
        return "grey UNREACHABLE"
    if not record.get("reachable"):
        return "grey no-ssh"
    unit = record.get("unit", "unknown")
    if not snapshot:
        # `systemctl is-active` also says "inactive" for a unit instance that was never
        # installed, so an absent status file is what distinguishes "not deployed" from
        # "finished". Without this an undeployed experiment reads as complete.
        return "green Running (starting)" if unit == "active" else "grey not deployed"
    if unit == "failed":
        return "red Failed"
    if snapshot.get("cancelled"):
        return "red Cancelled"
    if snapshot.get("stopped_reason") == "experiment_cost_cap":
        return "amber CAP-STOP" if unit == "active" else "amber Capped"
    if snapshot.get("stopped_reason"):
        return "amber Stopping" if unit == "active" else "amber Stopped"
    if unit == "active":
        stage = snapshot.get("stage", "")
        # `prepare` can take hours (SWE-bench pulls its whole image set), so name the stage
        # rather than showing a run that looks stuck at 0 tasks.
        return "green Running" if stage == "execute" else f"green {stage or 'Running'}"
    if snapshot.get("done") and snapshot.get("done") == snapshot.get("total"):
        return "blue Complete"
    return "blue Finished"


ICONS = {"green": "🟢", "amber": "🟡", "red": "🔴", "blue": "🔵", "grey": "⚪"}


def _age(record: dict[str, Any]) -> str:
    stamp = record.get("polled_at")
    if not isinstance(stamp, int | float):
        return "-"
    seconds = max(0, int(time.time() - stamp))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def render(experiments: list[Experiment], records: dict[str, dict[str, Any]]) -> str:
    header = (
        f"{'Experiment':<34}{'Status':<16}{'Done':>10}{'Run':>5}{'Fail':>6}"
        f"{'Cost':>11}{'Cap':>8}{'%Bud':>7}{'Age':>6}"
    )
    lines = [header, "-" * len(header)]
    total_cost = 0.0
    total_cap = 0.0
    incomplete_cost = False

    for experiment in experiments:
        record = records.get(experiment.label, {"label": experiment.label})
        snapshot = record.get("snapshot") or {}
        colour, text = _status_icon(record).split(" ", 1)
        status = f"{ICONS[colour]} {text}"

        done = snapshot.get("done")
        total = snapshot.get("total")
        progress = f"{done}/{total}" if done is not None and total else "-"
        cost = snapshot.get("cost_usd")
        cap = snapshot.get("experiment_cap_usd") or experiment.experiment_cap_usd
        total_cap += float(cap)
        if isinstance(cost, int | float):
            total_cost += float(cost)
            cost_text = f"${cost:,.2f}"
            if snapshot.get("cost_complete") is False:
                cost_text += "+"  # a call could not be priced, so true spend is at least this
                incomplete_cost = True
        else:
            cost_text = "-"
        pct = snapshot.get("pct_budget")
        pct_text = f"{pct:.0f}%" if isinstance(pct, int | float) else "-"

        lines.append(
            f"{experiment.label:<34}{status:<16}{progress:>10}"
            f"{snapshot.get('running', '-') or 0:>5}{snapshot.get('failed', '-') or 0:>6}"
            f"{cost_text:>11}{'$' + format(int(cap), ','):>8}{pct_text:>7}{_age(record):>6}"
        )

    lines.append("-" * len(header))
    share = f"{100 * total_cost / total_cap:.0f}%" if total_cap else "-"
    suffix = "+" if incomplete_cost else ""
    lines.append(
        f"{'TOTAL':<34}{'':<16}{'':>10}{'':>5}{'':>6}"
        f"{'$' + format(total_cost, ',.2f') + suffix:>11}"
        f"{'$' + format(int(total_cap), ','):>8}{share:>7}{'':>6}"
    )
    unrun = sum((records.get(e.label, {}).get("snapshot") or {}).get("unrun", 0) for e in experiments)
    if unrun:
        lines.append(f"\n{unrun} task(s) unrun across the fleet (stopped before they started).")
    if incomplete_cost:
        lines.append("\n'+' marks a total that omits at least one call that could not be priced.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- commands


def cmd_status(args: argparse.Namespace) -> int:
    experiments = load_experiments()
    print(render(experiments, merge_with_cache(poll_all(experiments))))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    experiments = load_experiments()
    try:
        while True:
            table = render(experiments, merge_with_cache(poll_all(experiments)))
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print("\033[2J\033[H", end="")
            print(f"agent-bench fleet — {stamp} (every {args.interval}s, Ctrl-C to exit)\n")
            print(table, flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_logs(args: argparse.Namespace) -> int:
    experiment = find(args.label)
    unit = f"agent-bench@{experiment.label}"
    remote = f"journalctl -u {unit} --no-pager -n {args.lines}"
    if args.follow:
        remote = f"journalctl -u {unit} -n {args.lines} -f"
    return subprocess.call(ssh_command(experiment, remote))


def cmd_ssh(args: argparse.Namespace) -> int:
    experiment = find(args.label)
    command = ssh_command(experiment)
    command.insert(1, "-t")
    remote_dir = f"{experiment.remote_root}/eval_engine"
    command.append(f"cd {remote_dir} && exec bash -l")
    return subprocess.call(command)


def cmd_stop(args: argparse.Namespace) -> int:
    """Ask an experiment to wind down cleanly, then grade and report what finished."""
    experiment = find(args.label)
    unit = f"agent-bench@{experiment.label}"
    # SIGTERM via systemctl stop is caught by the CLI and recorded as a cooperative stop; the
    # controller's exit trap then finalizes. Nothing in flight is killed and nothing is deleted.
    print(f"stopping {experiment.label} on {experiment.vm} (draining, then grading)...")
    return subprocess.call(ssh_command(experiment, f"sudo systemctl stop {unit}"))


def cmd_labels(args: argparse.Namespace) -> int:
    for experiment in load_experiments():
        print(f"{experiment.label:<34} {experiment.vm:<28} {experiment.benchmark}")
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    """Emit one experiment's row as shell assignments, for deploy_experiment.sh to eval."""
    experiment = find(args.label)
    values = {
        "FLEET_LABEL": experiment.label,
        "FLEET_VM": experiment.vm,
        "FLEET_VM_IP": vm_ip(experiment),
        "FLEET_BENCHMARK": experiment.benchmark,
        "FLEET_MODEL": experiment.model,
        "FLEET_PROVIDER": experiment.provider,
        "FLEET_API_KEY_FROM": experiment.api_key_from,
        "FLEET_EXPERIMENT_CAP_USD": f"{experiment.experiment_cap_usd:g}",
        "FLEET_PER_TASK_CAP_USD": (
            f"{experiment.per_task_cap_usd:g}" if experiment.per_task_cap_usd else ""
        ),
        "FLEET_WORKERS": str(experiment.workers),
        "FLEET_REMOTE_ROOT": experiment.remote_root,
        "FLEET_SSH_USER": experiment.ssh_user,
        "FLEET_DEPENDENCY_EXTRA": experiment.dependency_extra,
        "FLEET_SSH_KEY": (
            os.path.expanduser("~/.ssh/google_compute_engine")
            if os.path.exists(os.path.expanduser("~/.ssh/google_compute_engine"))
            else ""
        ),
    }
    for key, value in values.items():
        print(f"{key}={value!r}".replace("'", '"'))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="one-shot fleet table").set_defaults(func=cmd_status)

    watch = sub.add_parser("watch", help="refreshing fleet table")
    watch.add_argument("--interval", type=int, default=60)
    watch.set_defaults(func=cmd_watch)

    logs = sub.add_parser("logs", help="the controller's journal for one experiment")
    logs.add_argument("label")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("-n", "--lines", type=int, default=80)
    logs.set_defaults(func=cmd_logs)

    shell = sub.add_parser("ssh", help="interactive shell on the experiment's VM")
    shell.add_argument("label")
    shell.set_defaults(func=cmd_ssh)

    stop = sub.add_parser("stop", help="drain one experiment cleanly, then grade and report")
    stop.add_argument("label")
    stop.set_defaults(func=cmd_stop)

    sub.add_parser("labels", help="list experiment labels").set_defaults(func=cmd_labels)

    env = sub.add_parser("env", help="shell assignments for one experiment")
    env.add_argument("label")
    env.set_defaults(func=cmd_env)

    args = parser.parse_args()
    if not shutil.which("gcloud"):
        sys.exit("gcloud is required to resolve VM addresses")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
