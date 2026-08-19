from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).with_name("data")
GRAPH_DIR = ROOT / "graph" / "glm5.3"
DRIVE_FOLDER_ID = "1SQAW8ZUqYClRTWotCuxwR290ram4woWp"
CACHE_DIR = ROOT / ".cache" / "glm5.3-drive"
PURPLE, ORANGE, GREEN = "#7d60b5", "#f05a24", "#00b84f"
BG, GRID, GREY = "#fbfbfa", "#deddd7", "#aaa9a3"
MODELS = ("GLM-5.3 xhigh", "Kimi-K3 max")
OUTPUT_FILENAMES = {
    1: "fig1_benchmark_family_accuracy_glm53_vs_kimi.png",
    2: "fig2_swebench_solver_demand_accuracy_average_cost_scatter_glm53_vs_kimi.png",
    3: "fig2_swebench_solver_demand_accuracy_glm53_vs_kimi.png",
    4: "fig2_terminal_bench_2_1_domain_accuracy_average_cost_scatter_glm53_vs_kimi.png",
    5: "fig2_terminal_bench_2_1_domain_accuracy_glm53_vs_kimi.png",
    6: "fig6_routing_glm53_vs_kimi.png",
    7: "fig8_combined_cost_vs_accuracy_glm53_vs_kimi_with_routing.png",
    8: "model_success_overlap_venn_glm53_vs_kimi.png",
    9: "pairwise_exclusive_successes_glm53_vs_kimi_swe_and_terminal.png",
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def drive_credentials(login: bool):
    from google.oauth2.credentials import Credentials

    if shutil.which("gcloud") is None:
        raise RuntimeError(
            "Google Cloud CLI is required. Install it from "
            "https://cloud.google.com/sdk/docs/install and try again."
        )
    if login:
        subprocess.run(
            ["gcloud", "auth", "login", "--enable-gdrive-access"], check=True
        )
    try:
        token = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Google login is required. Run this script again with --login."
        ) from error
    return Credentials(token=token)


def download_from_drive(filename: str, login: bool) -> Path:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    service = build("drive", "v3", credentials=drive_credentials(login))
    escaped = filename.replace("'", "\\'")
    response = (
        service.files()
        .list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and name = '{escaped}' and trashed = false",
            fields="files(id,name,mimeType)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
    )
    files = response.get("files", [])
    if len(files) != 1:
        raise FileNotFoundError(
            f"Expected exactly one {filename} in Drive folder {DRIVE_FOLDER_ID}; "
            f"found {len(files)}"
        )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / filename
    request = service.files().get_media(fileId=files[0]["id"], supportsAllDrives=True)
    with target.open("wb") as stream:
        downloader = MediaIoBaseDownload(stream, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return target


def input_path(
    filename: str,
    supplied: Path | None,
    refresh: bool,
    login: bool,
) -> Path:
    if supplied:
        return supplied.resolve()
    target = CACHE_DIR / filename
    if login or refresh:
        return download_from_drive(filename, login)
    if target.exists():
        return target
    local = DATA_DIR / filename
    if local.exists():
        return local
    raise RuntimeError(
        "No cached or local CSV was found. Run this script with --login --refresh."
    )


def setup(title: str, size=(15.0, 8.0)):
    fig, ax = plt.subplots(figsize=size, dpi=150)
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    fig.text(.008, .978, title, va="top", fontsize=18, fontweight="bold", family="monospace")
    return fig, ax


def finish(fig, ax, output: Path):
    ax.grid(color=GRID, linewidth=.8); ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(length=0, colors="#77756f", labelsize=11)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def cli(figure: int, filename: str, draw):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path)
    parser.add_argument(
        "--output", type=Path, default=GRAPH_DIR / OUTPUT_FILENAMES[figure]
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open Google login through gcloud before downloading",
    )
    args = parser.parse_args()
    draw(
        rows(
            input_path(
                filename,
                args.data,
                args.refresh,
                args.login,
            )
        ),
        args.output.resolve(),
    )
    print(args.output.resolve())


def model_color(model: str) -> str:
    return PURPLE if model.startswith("GLM") else ORANGE


def legend_models(ax, routing=False, **kwargs):
    labels = list(MODELS) + (["Route per task"] if routing else [])
    colors = [PURPLE, ORANGE] + ([GREEN] if routing else [])
    ax.legend([Patch(color=c) for c in colors], labels, frameon=False, **kwargs)
