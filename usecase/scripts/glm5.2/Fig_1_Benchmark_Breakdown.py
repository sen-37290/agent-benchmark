#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["matplotlib==3.10.5", "numpy==2.3.2", "google-api-python-client==2.179.0"]
# ///
"""Download the Figure 1 inputs and generate the benchmark breakdown chart."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from _drive import get_data_file

# Figure configuration: edit these values to change the presentation.
TITLE = "TOTAL ACCURACY GROUPED BY BENCHMARK FAMILY"
COLORS = {"GLM-5.2 xhigh": "#2f78d2", "Kimi-K3 max": "#ef6632"}
MODEL_ORDER = ("GLM-5.2 xhigh", "Kimi-K3 max")
FIGURE_SIZE = (12.8, 7.2)
DPI = 160
OUTPUT_FILENAME = "Fig_1_Benchmark_Breakdown.png"

DATA_FILENAME = "fig_1_benchmark_breakdown.csv"
DATA_SHA256 = "cd56597c1fd66f445f8dd27ea795eb221e5f7bed0a8d8ae53d48c2de7b91abd9"

USECASE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = USECASE_ROOT / "graph" / "glm5.2" / OUTPUT_FILENAME
os.environ.setdefault("MPLCONFIGDIR", str(USECASE_ROOT / ".cache" / "matplotlib"))



import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch


def get_data(local_path: Path | None, refresh: bool, login: bool) -> Path:
    return get_data_file(DATA_FILENAME, DATA_SHA256, local_path, refresh, login)

def load_rows(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    required = {"benchmark", "model", "resolved", "total"}
    if not raw or not required.issubset(raw[0]):
        raise ValueError(f"{path} must contain columns: {sorted(required)}")
    rows = []
    for row in raw:
        resolved, total = int(row["resolved"]), int(row["total"])
        if not 0 <= resolved <= total:
            raise ValueError(f"Invalid score for {row['benchmark']} / {row['model']}")
        rows.append({**row, "resolved": resolved, "total": total})
    if len(rows) != 4:
        raise ValueError(f"Expected 4 benchmark/model rows, found {len(rows)}")
    return rows


def draw(rows: list[dict[str, object]], output: Path) -> None:
    families = ("SWE-bench Verified", "Terminal-Bench 2.1")
    scores = {(str(row["benchmark"]), str(row["model"])): row for row in rows}
    centers = np.array([0.0, 1.55])
    offsets = {"GLM-5.2 xhigh": -0.14, "Kimi-K3 max": 0.14}

    fig, ax = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    for family_index, family in enumerate(families):
        for model in MODEL_ORDER:
            row = scores.get((family, model))
            if row is None:
                continue
            value = 100.0 * int(row["resolved"]) / int(row["total"])
            x = centers[family_index] + offsets[model]
            ax.bar(
                x,
                value,
                width=0.25,
                color=COLORS[model],
                label=model if family_index == 0 else None,
            )
            ax.text(
                x,
                value + 1.8,
                f"{value:.1f}",
                ha="center",
                fontsize=12,
                fontweight="bold",
                color=COLORS[model],
            )
            ax.text(
                x,
                -5.0,
                f"{row['resolved']}/{row['total']}",
                ha="center",
                va="top",
                color="#777A89",
            )
    ax.set_title(TITLE, loc="left", pad=28, fontsize=18, family="monospace")
    ax.set_ylabel("Accuracy (%)", fontsize=13, fontweight="bold")
    ax.set_xticks(centers, families, fontsize=14, fontweight="bold")
    ax.tick_params(axis="x", pad=26, length=0)
    ax.tick_params(axis="y", labelsize=11, length=0)
    ax.set_ylim(-10, 108)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.grid(axis="y", color="#ECECF2", linewidth=1)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    handles = [Patch(color=COLORS[name], label=name) for name in MODEL_ORDER]
    ax.legend(
        handles=handles,
        frameon=False,
        ncol=2,
        loc="upper right",
        bbox_to_anchor=(1.0, 1.12),
        fontsize=11,
    )
    fig.subplots_adjust(left=0.09, right=0.98, top=0.82, bottom=0.18)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", facecolor="white")
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
    data = get_data(args.data, args.refresh, args.login)
    draw(load_rows(data), args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
