#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["matplotlib==3.10.5", "numpy==2.3.2", "google-api-python-client==2.179.0"]
# ///
"""Download the Figure 7 inputs and generate the domain difference chart."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from _drive import get_data_file

# Figure configuration: edit these values to change the presentation.
TITLE = "Terminal-Bench 2.1: accuracy difference by domain"
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
LEFT_MODEL = "GLM-5.2 xhigh"
RIGHT_MODEL = "Kimi-K3 max"
SURFACE, MUTED, GRID = "#fbfbfa", "#77756f", "#deddd7"
KIMI, GLM = "#e6612b", "#2f78d2"
MONO = "DejaVu Sans Mono"
FIGURE_SIZE = (12.8, 6.8)
DPI = 170
OUTPUT_FILENAME = "Fig_7_Terminal_Bench_Domain_Difference.png"

DATA_FILENAME = "fig_7_terminal_bench_domain_difference.csv"
DATA_SHA256 = "5ba747b8643ad8d5816d028ca3fbadeb29b938baf7de82a87dd41c550c42c663"
USECASE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = USECASE_ROOT / "graph" / "glm5.2" / OUTPUT_FILENAME
os.environ.setdefault("MPLCONFIGDIR", str(USECASE_ROOT / ".cache" / "matplotlib"))



import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def get_data(local_path: Path | None, refresh: bool, login: bool) -> Path:
    return get_data_file(DATA_FILENAME, DATA_SHA256, local_path, refresh, login)

def load_margins(path: Path) -> list[tuple[str, float]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    required = {"model", "domain", "solved", "total", "accuracy_pct"}
    if not raw or not required.issubset(raw[0]):
        raise ValueError(f"{path} must contain columns: {sorted(required)}")

    expected = {
        (model, domain) for model in (LEFT_MODEL, RIGHT_MODEL) for domain in DOMAINS
    }
    if len(raw) != 16 or {(row["model"], row["domain"]) for row in raw} != expected:
        raise ValueError("Figure 7 requires exactly two model values for each domain")

    values: dict[tuple[str, str], float] = {}
    for row in raw:
        solved, total = int(row["solved"]), int(row["total"])
        rate = float(row["accuracy_pct"])
        if not 0 <= solved <= total or abs(rate - 100 * solved / total) > 1e-9:
            raise ValueError(f"Invalid score for {row['model']} / {row['domain']}")
        values[(row["model"], row["domain"])] = rate
    return [
        (domain, values[(RIGHT_MODEL, domain)] - values[(LEFT_MODEL, domain)])
        for domain in DOMAINS
    ]


def draw(margins: list[tuple[str, float]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for yi, (domain, margin) in enumerate(margins):
        if margin == 0:
            continue
        color = KIMI if margin > 0 else GLM
        ax.barh(yi, margin, height=0.48, color=color, zorder=3)
        ax.text(
            margin + (0.9 if margin > 0 else -0.9),
            yi,
            f"{margin:+.1f}",
            ha="left" if margin > 0 else "right",
            va="center",
            color=color,
            fontsize=11,
            fontfamily=MONO,
            fontweight="bold",
        )

    ax.axvline(0, color="#b9b7b0", lw=1.2, zorder=2)
    ax.set_xlim(-55, 60)
    ax.set_xticks([-50, -25, 0, 25, 50])
    ax.xaxis.grid(True, color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_yticks(range(len(margins)))
    ax.set_yticklabels(
        [DOMAIN_LABELS[domain] for domain, _ in margins],
        fontsize=11.5,
        fontweight="bold",
    )
    ax.invert_yaxis()
    ax.tick_params(axis="both", length=0, colors=MUTED, labelsize=10)
    ax.set_xlabel("Accuracy difference (percentage points)", color=MUTED, fontsize=11)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)

    fig.text(
        0.02,
        0.97,
        TITLE,
        fontsize=16,
        fontweight="bold",
        fontfamily=MONO,
        va="top",
    )
    fig.subplots_adjust(left=0.18, right=0.96, top=0.88, bottom=0.13)
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
    draw(load_margins(get_data(args.data, args.refresh, args.login)), args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
