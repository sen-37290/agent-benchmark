#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["matplotlib==3.10.5", "numpy==2.3.2", "google-api-python-client==2.179.0"]
# ///
"""Download the Figure 3 inputs and generate the cost-per-task chart."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from _drive import get_data_file

# Figure configuration: edit these values to change the presentation.
TITLE = "COST VS. SOLVE RATE — SWE-BENCH VERIFIED + TERMINAL-BENCH 2.1"
COLORS = {"GLM-5.2 xhigh": "#2f78d2", "Kimi-K3 max": "#ef6632"}
SURFACE, INK, MUTED, GRID, CONNECT = (
    "#fbfbfa",
    "#111111",
    "#8d8a82",
    "#deddd7",
    "#b8b6ae",
)
FIGURE_SIZE = (12.4, 7.0)
DPI = 180
OUTPUT_FILENAME = "Fig_3_Cost_Per_Task.png"

DATA_FILENAME = "fig_3_cost_per_task.csv"
DATA_SHA256 = "466c30470aafc613474ca1b673aea997acaf691546efe55d2893e607ad38f0fa"
USECASE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = USECASE_ROOT / "graph" / "glm5.2" / OUTPUT_FILENAME
EXPECTED_TASKS = {"SWE-bench Verified": 500, "Terminal-Bench 2.1": 89}
os.environ.setdefault("MPLCONFIGDIR", str(USECASE_ROOT / ".cache" / "matplotlib"))


import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter


def get_data(local_path: Path | None, refresh: bool, login: bool) -> Path:
    return get_data_file(DATA_FILENAME, DATA_SHA256, local_path, refresh, login)

def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"Expected true/false, got {value!r}")
    return normalized == "true"


def load_rows(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    required = {"benchmark", "task_id", "model", "resolved", "generation_cost_usd"}
    if not raw or not required.issubset(raw[0]):
        raise ValueError(f"{path} must contain columns: {sorted(required)}")
    rows = []
    for row in raw:
        parsed = {
            **row,
            "resolved": parse_bool(row["resolved"]),
            "generation_cost_usd": float(row["generation_cost_usd"]),
        }
        if parsed["generation_cost_usd"] < 0:
            raise ValueError(
                f"Negative generation cost for {row['benchmark']} / {row['task_id']}"
            )
        rows.append(parsed)
    if (
        len(rows) != 1178
        or len({(r["benchmark"], r["task_id"], r["model"]) for r in rows}) != 1178
    ):
        raise ValueError("Figure 3 requires 1,178 unique benchmark/task/model rows")
    return rows


def task_cost(row: dict[str, object]) -> float:
    return float(row["generation_cost_usd"])


def summarize(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    summary = []
    for benchmark, expected in EXPECTED_TASKS.items():
        for model in COLORS:
            group = [
                row
                for row in rows
                if row["benchmark"] == benchmark and row["model"] == model
            ]
            if (
                len(group) != expected
                or len({row["task_id"] for row in group}) != expected
            ):
                raise ValueError(f"Expected {expected} {benchmark} rows for {model}")
            solved = sum(bool(row["resolved"]) for row in group)
            summary.append(
                {
                    "benchmark": benchmark,
                    "model": model,
                    "solve_rate": 100 * solved / expected,
                    "cost_per_task": sum(task_cost(row) for row in group) / expected,
                }
            )
    return summary


def draw(summary: list[dict[str, object]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    styles = {"SWE-bench Verified": ("o", "-"), "Terminal-Bench 2.1": ("D", "--")}
    for benchmark, (marker, linestyle) in styles.items():
        points = sorted(
            (row for row in summary if row["benchmark"] == benchmark),
            key=lambda row: float(row["cost_per_task"]),
        )
        ax.plot(
            [row["cost_per_task"] for row in points],
            [row["solve_rate"] for row in points],
            linestyle,
            color=CONNECT,
            lw=1.8,
            zorder=2,
        )
        for point in points:
            ax.plot(
                point["cost_per_task"],
                point["solve_rate"],
                marker,
                ms=11,
                color=COLORS[str(point["model"])],
                markeredgecolor=SURFACE,
                markeredgewidth=1.8,
                zorder=4,
            )
            ax.annotate(
                f"{point['solve_rate']:.1f}%   ${point['cost_per_task']:.3f}",
                (point["cost_per_task"], point["solve_rate"]),
                xytext=(0, 9),
                textcoords="offset points",
                ha="center",
                fontfamily="DejaVu Sans Mono",
            )
    costs = [float(row["cost_per_task"]) for row in summary]
    padding = max(0.05, 0.08 * (max(costs) - min(costs)))
    ax.set_xlim(min(costs) - padding, max(costs) + padding)
    ax.set_ylim(60, 102)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"${value:.2f}"))
    ax.set_ylabel("Task solve rate (%)", fontsize=12, fontweight="bold", color=INK)
    ax.set_xlabel("Mean cumulative generation cost per selected task", color=MUTED)
    ax.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    model_handles = [
        Line2D([], [], marker="s", ls="", ms=10, color=color, label=model)
        for model, color in COLORS.items()
    ]
    benchmark_handles = [
        Line2D(
            [], [], marker="o", ls="-", color=CONNECT, ms=8, label="SWE-bench Verified"
        ),
        Line2D(
            [], [], marker="D", ls="--", color=CONNECT, ms=7, label="Terminal-Bench 2.1"
        ),
    ]
    model_legend = ax.legend(
        handles=model_handles, title="Model", loc="lower right", frameon=False
    )
    ax.add_artist(model_legend)
    ax.legend(
        handles=benchmark_handles, title="Benchmark", loc="lower center", frameon=False
    )
    fig.suptitle(TITLE, x=0.012, y=0.975, ha="left", fontsize=15, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=DPI, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", type=Path, help="Use a local CSV instead of Google Drive"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--refresh", action="store_true", help="Redownload the Drive folder"
    )
    parser.add_argument(
        "--login", action="store_true", help="Open Google login through gcloud"
    )
    args = parser.parse_args()
    draw(summarize(load_rows(get_data(args.data, args.refresh, args.login))), args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
