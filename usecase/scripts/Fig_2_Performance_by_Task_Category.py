#!/usr/bin/env python3
"""Download the Figure 2 inputs and generate the task-category margin chart."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

# Figure configuration: edit these values to change the presentation.
TITLE = "Where each model leads across final solver-demand classes"
COLORS = {"GLM-5.2 xhigh": "#2f78d2", "Kimi-K3 max": "#ef6632"}
SURFACE, INK, MUTED, GRID = "#fbfbfa", "#111111", "#8d8a82", "#deddd7"
FIGURE_SIZE = (13.8, 7.6)
DPI = 150
OUTPUT_FILENAME = "Fig_2_Performance_by_Task_Category.png"
CLASS_LABELS = {
    "DATA_FIDELITY_PROBLEMS": "Data fidelity problems",
    "TRACING_AND_OBSERVABILITY_PROBLEMS": "Tracing & observability problems",
    "COMPATIBILITY_PROBLEMS": "Compatibility problems",
    "RENDERING_AND_VISUAL_PROBLEMS": "Rendering & visual problems",
    "PARSING_PROBLEMS": "Parsing problems",
}

DRIVE_FOLDER_URL = (
    "https://drive.google.com/drive/folders/1d3dawCzoHHlanEN7HR54EmBRs_rMfEnF"
)
DATA_FILENAME = "fig_2_performance_by_task_category.csv"
DATA_SHA256 = "4b68343e16cc9dcb6a2b0c4ff3e6a5cf72308b65751a33846e4eb3abbfb5829a"
DEPENDENCIES = ("matplotlib==3.11.1", "gdown==5.2.0", "urllib3==1.26.20")
USECASE_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = USECASE_ROOT / ".cache" / "fig_2_performance_by_task_category"
DEFAULT_OUTPUT = USECASE_ROOT / "graph" / OUTPUT_FILENAME
os.environ.setdefault("MPLCONFIGDIR", str(USECASE_ROOT / ".cache" / "matplotlib"))


def ensure_dependencies() -> None:
    try:
        import matplotlib  # noqa: F401

        if not any(
            arg == "--data" or arg.startswith("--data=") for arg in sys.argv[1:]
        ):
            import gdown  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    environment = USECASE_ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists() and Path(sys.prefix).resolve() != environment.resolve():
        os.execv(
            str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]]
        )
    if not python.exists():
        print(f"Creating local Python environment at {environment}")
        venv.EnvBuilder(with_pip=True).create(environment)
    print("Installing pinned graph dependencies (first run only)")
    subprocess.check_call(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *DEPENDENCIES,
        ]
    )
    os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


ensure_dependencies()
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_data(local_path: Path | None, refresh: bool) -> Path:
    if local_path is not None:
        path = local_path.resolve()
    else:
        matches = list(CACHE_DIR.rglob(DATA_FILENAME)) if CACHE_DIR.exists() else []
        valid = len(matches) == 1 and sha256(matches[0]) == DATA_SHA256
        if refresh or not valid:
            import gdown

            if CACHE_DIR.exists():
                shutil.rmtree(CACHE_DIR)
            CACHE_DIR.mkdir(parents=True)
            print("Downloading public figure inputs from Google Drive")
            gdown.download_folder(
                url=DRIVE_FOLDER_URL,
                output=str(CACHE_DIR),
                quiet=False,
                remaining_ok=True,
            )
            matches = list(CACHE_DIR.rglob(DATA_FILENAME))
        if len(matches) != 1:
            raise FileNotFoundError(
                f"Expected exactly one {DATA_FILENAME} in the Drive folder, found {len(matches)}"
            )
        path = matches[0]
    actual = sha256(path)
    if actual != DATA_SHA256:
        raise ValueError(
            f"Checksum mismatch for {path}: expected {DATA_SHA256}, got {actual}"
        )
    return path


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"Expected true/false, got {value!r}")
    return normalized == "true"


def load_rows(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    required = {
        "task_id",
        "primary_solver_demand_class",
        "glm_resolved",
        "kimi_resolved",
    }
    if not raw or not required.issubset(raw[0]):
        raise ValueError(f"{path} must contain columns: {sorted(required)}")
    if len(raw) != 500 or len({row["task_id"] for row in raw}) != 500:
        raise ValueError("Figure 2 requires exactly 500 unique SWE-bench tasks")
    if {row["primary_solver_demand_class"] for row in raw} != set(CLASS_LABELS):
        raise ValueError("Unexpected solver-demand classes")
    return [
        {
            **row,
            "glm_resolved": parse_bool(row["glm_resolved"]),
            "kimi_resolved": parse_bool(row["kimi_resolved"]),
        }
        for row in raw
    ]


def category_scores(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output = []
    for raw_class, label in CLASS_LABELS.items():
        group = [row for row in rows if row["primary_solver_demand_class"] == raw_class]
        glm = sum(bool(row["glm_resolved"]) for row in group)
        kimi = sum(bool(row["kimi_resolved"]) for row in group)
        total = len(group)
        output.append(
            {
                "domain": label,
                "total": total,
                "glm_rate": 100 * glm / total,
                "kimi_rate": 100 * kimi / total,
            }
        )
    return output


def draw(scores: list[dict[str, object]], output: Path) -> None:
    max_margin = max(
        abs(float(row["kimi_rate"]) - float(row["glm_rate"])) for row in scores
    )
    axis_limit = max(20, int((max_margin + 9) // 10 + 1) * 10)
    frame = scores
    fig, ax = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for yi, row in enumerate(frame):
        margin = float(row["kimi_rate"]) - float(row["glm_rate"])
        if margin > 0:
            winner, color = "Kimi-K3", COLORS["Kimi-K3 max"]
        elif margin < 0:
            winner, color = "GLM-5.2", COLORS["GLM-5.2 xhigh"]
        else:
            winner, color = "Tie", MUTED
        ax.barh(yi, margin, height=0.55, color=color, zorder=3)
        label_x = margin + (0.7 if margin >= 0 else -0.7)
        align = "left" if margin >= 0 else "right"
        label = f"{winner} {abs(margin):+.1f}" if winner != "Tie" else "Tie 0.0"
        ax.text(
            label_x,
            yi + 0.13,
            label,
            ha=align,
            va="top",
            color=color,
            fontweight="bold",
            fontfamily="DejaVu Sans Mono",
        )
        ax.text(
            label_x,
            yi - 0.13,
            f"{row['glm_rate']:.1f} vs {row['kimi_rate']:.1f}  (n={row['total']})",
            ha=align,
            va="bottom",
            color=INK,
            fontfamily="DejaVu Sans Mono",
        )
    ax.axvline(0, color=GRID, lw=1.2, zorder=1)
    ax.set_xlim(-axis_limit, axis_limit)
    ax.xaxis.grid(True, color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, colors=MUTED)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.text(
        0.0,
        1.015,
        "← GLM-5.2 xhigh ahead      Kimi-K3 max ahead →",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        color=MUTED,
        fontsize=10,
        fontfamily="DejaVu Sans Mono",
    )
    ax.set_yticks(range(len(frame)))
    ax.set_yticklabels(
        [str(row["domain"]) for row in frame], fontsize=10.8, fontweight="bold"
    )
    ax.invert_yaxis()
    ax.set_xlabel("Percentage-point margin", color=MUTED)
    fig.suptitle(TITLE, y=0.98, fontfamily="DejaVu Sans Mono", fontweight="bold")
    fig.subplots_adjust(left=0.30, right=0.97, top=0.88, bottom=0.11)
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
    args = parser.parse_args()
    draw(
        category_scores(load_rows(get_data(args.data, args.refresh))),
        args.output.resolve(),
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
