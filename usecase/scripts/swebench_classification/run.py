"""One-command runner for the SWE-bench issue/PR classification pipeline."""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
REQUIRED_MODULES = ("openai", "openpyxl", "yaml", "jsonschema", "datasets")


def ensure_dependencies() -> None:
    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    if not missing:
        return
    if os.environ.get("SWEBENCH_CLASSIFICATION_UV_READY") == "1":
        raise SystemExit(f"uv environment is still missing modules: {', '.join(missing)}")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv was not found on PATH. Install uv, then run this script again.")
    print(f"Starting a uv environment with required modules: {', '.join(missing)}")
    command = [
        uv,
        "run",
        "--python",
        "3.13",
        "--with-requirements",
        str(REQUIREMENTS),
        "python",
        str(Path(__file__).resolve()),
        *sys.argv[1:],
    ]
    environment = os.environ.copy()
    environment["SWEBENCH_CLASSIFICATION_UV_READY"] = "1"
    environment.setdefault("UV_CACHE_DIR", "/tmp/swebench-classification-uv-cache")
    os.execve(uv, command, environment)


def ensure_api_key() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    if not sys.stdin.isatty():
        raise SystemExit(
            "OPENAI_API_KEY is not set. Export it before running this script, for example:\n"
            "  export OPENAI_API_KEY='sk-...'"
        )
    key = getpass.getpass("OpenAI API key (hidden): ").strip()
    if not key:
        raise SystemExit("No API key was provided.")
    os.environ["OPENAI_API_KEY"] = key


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read scraped GitHub issue/PR text and classify SWE-bench cases."
    )
    parser.add_argument(
        "--phase",
        choices=("validate", "prepare", "all"),
        default="prepare",
        help="validate input only, or resume/produce the task-shape classification",
    )
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_dependencies()

    from pipeline import (  # Imported only after uv dependency installation.
        OpenAIResponsesLLM,
        enrich_with_verified_dataset,
        load_config,
        run_until_human_review,
        validate_and_convert_xlsx,
    )

    config = load_config(args.config)
    output_dir = Path(config["output_dir"])

    if args.phase == "validate":
        normalized = validate_and_convert_xlsx(config)
        path = enrich_with_verified_dataset(config, normalized)
        print(f"Validated XLSX, matched SWE-bench Verified, and wrote: {path}")
        return

    ensure_api_key()
    llm = OpenAIResponsesLLM(config, output_dir / "_llm_cache")

    outputs = run_until_human_review(config, llm)
    print("\nComplete:")
    for name, path in outputs.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
