# Reproducible figures

This directory contains the two analysis notebooks and five standalone figure scripts. Generated
images are written to `graph/` and are intentionally excluded from Git.

## Run a figure

### 1. Check your Python version

Python 3.11 or newer is required:

```bash
python3 --version
```

If the version is lower than 3.11, install Python 3.11:

```bash
brew install python@3.11
```

### 2. Run a figure

From the repository root, run the desired script with `python3.11`:

```bash
python3.11 usecase/scripts/Fig_1_Benchmark_Breakdown.py
python3.11 usecase/scripts/Fig_2_Performance_by_Task_Category.py
python3.11 usecase/scripts/Fig_3_Cost_Per_Task.py
python3.11 usecase/scripts/Fig_4_Routing.py
python3.11 usecase/scripts/Fig_5_Solve_Rate_Versus_Cost_With_Routing.py
```

No uv setup or manual package installation is required. On the first run, the script creates
`usecase/.venv`, installs pinned plotting and download dependencies, downloads only that figure's
public Google Drive subfolder, validates its input checksum, and generates the PNG. Later runs reuse
the local environment and that figure's download under `usecase/.cache/`.

Google authentication is not required because the source folder is shared as “Anyone with the link
— Viewer.” Network access is required on the first run.

## Options

Every script accepts the same operational options:

```text
--data PATH    Use a local CSV instead of downloading from Drive.
--output PATH  Write the PNG to a custom location.
--refresh      Redownload only this figure's public Drive subfolder before plotting.
```

Titles, colors, dimensions, DPI, and output filenames are grouped in the configuration block near
the top of each script so visual changes are easy to make.

Figures 3 and 5 preserve their selected reference charts' recorded/estimated generation-cost
baseline. Their input CSVs store those per-task costs explicitly so both charts remain reproducible.

## Layout

```text
usecase/
├── graph/      # generated PNGs; only .gitkeep is tracked
├── notebooks/  # exploratory analysis notebooks
└── scripts/    # exactly one Python script per figure
```
