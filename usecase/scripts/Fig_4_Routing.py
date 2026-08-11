#!/usr/bin/env python3
"""Download the Figure 4 inputs and generate the per-task routing chart."""

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
TITLE = "PER-TASK ROUTING VS EACH MODEL ALONE  •  GREEN TAGS = POINTS GAINED OVER THE BEST SINGLE MODEL"
COLORS = {"GLM-5.2": "#6C6FE3", "Kimi-K3": "#FF3D43", "Routing": "#00B84F"}
FIGURE_SIZE = (12.8, 7.2)
DPI = 180
OUTPUT_FILENAME = "Fig_4_Routing.png"

DRIVE_FOLDER_URL = (
    "https://drive.google.com/drive/folders/1JNn58RrMJsDz0G56720J9UrZwKq6TOgG"
)
DATA_FILENAME = "fig_4_routing.csv"
DATA_SHA256 = "0a757d20ff9a19580e21c23458b50e041fac2aea7302435a11c30b9eb8781c18"
DEPENDENCIES = ("matplotlib==3.11.1", "gdown==5.2.0", "urllib3==1.26.20")
USECASE_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = USECASE_ROOT / ".cache" / "fig_4_routing"
DEFAULT_OUTPUT = USECASE_ROOT / "graph" / OUTPUT_FILENAME
EXPECTED_TASKS = {"SWE-bench Verified": 500, "Terminal-Bench 2.1": 89}
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
import numpy as np


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


def summarize(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    required = {"benchmark", "task_id", "glm_resolved", "kimi_resolved"}
    if not raw or not required.issubset(raw[0]):
        raise ValueError(f"{path} must contain columns: {sorted(required)}")
    if (
        len(raw) != 589
        or len({(row["benchmark"], row["task_id"]) for row in raw}) != 589
    ):
        raise ValueError("Figure 4 requires 589 unique benchmark/task rows")
    output = []
    for benchmark, expected in EXPECTED_TASKS.items():
        group = [row for row in raw if row["benchmark"] == benchmark]
        if len(group) != expected:
            raise ValueError(f"Expected {expected} tasks for {benchmark}")
        glm = [parse_bool(row["glm_resolved"]) for row in group]
        kimi = [parse_bool(row["kimi_resolved"]) for row in group]
        glm_solved, kimi_solved = sum(glm), sum(kimi)
        routed = sum(left or right for left, right in zip(glm, kimi))
        output.append(
            {
                "benchmark": benchmark,
                "glm": 100 * glm_solved / expected,
                "kimi": 100 * kimi_solved / expected,
                "routing": 100 * routed / expected,
                "gain": 100 * (routed - max(glm_solved, kimi_solved)) / expected,
            }
        )
    return output


def fmt(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def draw(summary: list[dict[str, object]], output: Path) -> None:
    families = [str(row["benchmark"]) for row in summary]
    values = np.array([[row["glm"], row["kimi"], row["routing"]] for row in summary])
    x = np.arange(len(families))
    width = 0.22
    fig, ax = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
    series = [(-width, "GLM-5.2", 0), (0, "Kimi-K3", 1), (width, "Routing", 2)]
    for offset, label, column in series:
        bars = ax.bar(
            x + offset,
            values[:, column],
            width=width * 0.9,
            color=COLORS[label],
            label=label,
        )
        for index, bar in enumerate(bars):
            text = fmt(values[index, column])
            if column == 2:
                text += f"  (+{fmt(float(summary[index]['gain']))})"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 1.2,
                text,
                ha="center",
                va="bottom",
                color=COLORS[label],
                fontsize=11,
                fontweight="bold",
            )
    ax.set_ylim(0, 105)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("Task solve rate (%)", fontsize=12, fontweight="bold")
    ax.set_xticks(x, families, fontsize=13, fontweight="bold")
    ax.grid(axis="y", color="#ECECF2", linewidth=1)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#666666")
    ax.legend(
        loc="upper left", bbox_to_anchor=(0, 1.13), ncol=3, frameon=False, fontsize=12
    )
    fig.suptitle(TITLE, x=0.08, y=0.98, ha="left", fontsize=13, family="monospace")
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
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
    draw(summarize(get_data(args.data, args.refresh)), args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
