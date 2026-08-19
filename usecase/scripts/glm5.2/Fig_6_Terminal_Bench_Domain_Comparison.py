#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["matplotlib==3.10.5", "numpy==2.3.2", "google-api-python-client==2.179.0"]
# ///
"""Download the Figure 6 inputs and generate the domain comparison chart."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from _drive import get_data_file

# Figure configuration: edit these values to change the presentation.
TITLE = "Terminal-Bench 2.1: final accuracy by classified task domain"
DOMAINS = (
    "security",
    "ML",
    "science",
    "database",
    "media",
    "algorithm",
    "languages",
    "network",
)
DOMAIN_LABELS = {
    "security": "Security",
    "ML": "ML",
    "science": "Science",
    "database": "Database",
    "media": "Media",
    "algorithm": "Algorithm",
    "languages": "Languages",
    "network": "Network",
}
MODEL_COLORS = {"GLM-5.2 xhigh": "#2f78d2", "Kimi-K3 max": "#ef6632"}
SURFACE, INK, MUTED, GRID = "#fbfbfa", "#111111", "#8d8a82", "#deddd7"
MONO = "DejaVu Sans Mono"
FIGURE_SIZE = (14.2, 9.2)
DPI = 150
OUTPUT_FILENAME = "Fig_6_Terminal_Bench_Domain_Comparison.png"

DATA_FILENAME = "fig_6_terminal_bench_domain_comparison.csv"
DATA_SHA256 = "5ba747b8643ad8d5816d028ca3fbadeb29b938baf7de82a87dd41c550c42c663"
USECASE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = USECASE_ROOT / "graph" / "glm5.2" / OUTPUT_FILENAME
os.environ.setdefault("MPLCONFIGDIR", str(USECASE_ROOT / ".cache" / "matplotlib"))



import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


def get_data(local_path: Path | None, refresh: bool, login: bool) -> Path:
    return get_data_file(DATA_FILENAME, DATA_SHA256, local_path, refresh, login)

def load_scores(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    required = {"model", "domain", "solved", "total", "accuracy_pct"}
    if not raw or not required.issubset(raw[0]):
        raise ValueError(f"{path} must contain columns: {sorted(required)}")

    expected = {(model, domain) for model in MODEL_COLORS for domain in DOMAINS}
    if len(raw) != 16 or {(row["model"], row["domain"]) for row in raw} != expected:
        raise ValueError("Figure 6 requires exactly two model values for each domain")

    scores: list[dict[str, object]] = []
    for row in raw:
        solved, total = int(row["solved"]), int(row["total"])
        rate = float(row["accuracy_pct"])
        if not 0 <= solved <= total or abs(rate - 100 * solved / total) > 1e-9:
            raise ValueError(f"Invalid score for {row['model']} / {row['domain']}")
        scores.append({**row, "solved": solved, "total": total, "accuracy_pct": rate})
    return scores


def lookup(
    scores: list[dict[str, object]], model: str, domain: str
) -> dict[str, object]:
    return next(
        row for row in scores if row["model"] == model and row["domain"] == domain
    )


def draw(scores: list[dict[str, object]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    height = 0.28
    for yi, domain in enumerate(DOMAINS):
        for (model, color), offset in zip(
            MODEL_COLORS.items(), (-height / 2, height / 2)
        ):
            row = lookup(scores, model, domain)
            rate = float(row["accuracy_pct"])
            ax.barh(yi + offset, rate, height=height - 0.035, color=color, zorder=3)
            ax.text(
                rate + 1.0,
                yi + offset,
                f"{rate:.1f}% ({row['solved']}/{row['total']})",
                va="center",
                fontsize=9.3,
                color=INK,
                fontfamily=MONO,
                fontweight="bold",
            )
    ax.set_yticks(range(len(DOMAINS)))
    ax.set_yticklabels(
        [DOMAIN_LABELS[domain] for domain in DOMAINS],
        fontsize=11.3,
        fontweight="bold",
    )
    ax.invert_yaxis()
    ax.set_xlim(0, 132)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Task success rate (%)", color=MUTED)
    ax.xaxis.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.legend(
        handles=[
            Patch(color=color, label=model) for model, color in MODEL_COLORS.items()
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
        ncol=2,
        frameon=False,
        prop={"family": MONO},
    )
    fig.text(
        0.005,
        0.975,
        TITLE,
        fontsize=15,
        fontweight="bold",
        fontfamily=MONO,
        va="top",
    )
    fig.subplots_adjust(left=0.22, right=0.97, top=0.89, bottom=0.09)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, facecolor=SURFACE, bbox_inches="tight")
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
    draw(load_scores(get_data(args.data, args.refresh, args.login)), args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
