# Reproduce the benchmark figures

Follow these steps from the root of this repository.

## 1. Install the required command-line tools

Check whether `uv` and the Google Cloud CLI are installed:

```bash
uv --version
gcloud --version
```

If a command is missing, install it:

- [Install uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Install the Google Cloud CLI](https://cloud.google.com/sdk/docs/install)

You do not need to install Python packages manually. `uv` handles them for each script.

## 2. Choose a GLM-5.3 figure

The available scripts are:

```text
Fig_1_Benchmark_Breakdown.py
Fig_2_SWE_Bench_Category_Cost_Accuracy.py
Fig_3_SWE_Bench_Category_Accuracy.py
Fig_4_Terminal_Bench_Domain_Cost_Accuracy.py
Fig_5_Terminal_Bench_Domain_Accuracy.py
Fig_6_Routing.py
Fig_7_Cost_Versus_Accuracy_With_Routing.py
Fig_8_Successful_Case_Overlap.py
Fig_9_Pairwise_Exclusive_Successes.py
```

## 3. Generate your chosen figure

On the first run, add `--login --refresh`. For example, to generate Figure 1:

```bash
uv run usecase/scripts/glm5.3/Fig_1_Benchmark_Breakdown.py --login --refresh
```

A browser will open. Sign in with your `@friendli.ai` Google account. The script then downloads
the required CSV from the organization Drive folder and creates:

```text
usecase/graph/glm5.3/fig1_benchmark_family_accuracy_glm53_vs_kimi.png
```

To generate another figure, change only the script name. For example:

```bash
uv run usecase/scripts/glm5.3/Fig_6_Routing.py --refresh
```

You normally need `--login` only once. Use `--refresh` whenever you want the latest data from
Drive.

## Run a figure again

The downloaded CSV is cached locally. To regenerate the same figure without downloading it again:

```bash
uv run usecase/scripts/glm5.3/Fig_1_Benchmark_Breakdown.py
```

## Generate all GLM-5.3 figures

This is optional:

```bash
for script in usecase/scripts/glm5.3/Fig_*.py; do
  uv run "$script" --refresh
done
```

## Use a local CSV

Google login is not required when you already have the required CSV. Pass its path with `--data`:

```bash
uv run usecase/scripts/glm5.3/Fig_1_Benchmark_Breakdown.py \
  --data /path/to/fig01_benchmark_family_accuracy.csv
```

## Choose a different output path

```bash
uv run usecase/scripts/glm5.3/Fig_1_Benchmark_Breakdown.py \
  --output /tmp/benchmark-breakdown.png
```

## Generate a GLM-5.2 figure

GLM-5.2 follows the same workflow as GLM-5.3. On the first run, add `--login --refresh`:

```bash
uv run usecase/scripts/glm5.2/Fig_1_Benchmark_Breakdown.py --login --refresh
```

Sign in with your `@friendli.ai` Google account when the browser opens. The generated PNG is
written under:

```text
usecase/graph/glm5.2/
```

To run the same figure again with its cached CSV:

```bash
uv run usecase/scripts/glm5.2/Fig_1_Benchmark_Breakdown.py
```

To download updated data, add `--refresh`. To generate a different GLM-5.2 figure, replace the
script name with another `Fig_*.py` file from `usecase/scripts/glm5.2/`.

## Troubleshooting

### Google Drive access is denied

Run the script with `--login` and sign in with an authorized `@friendli.ai` account:

```bash
uv run usecase/scripts/glm5.3/Fig_1_Benchmark_Breakdown.py --login --refresh
```

Personal Gmail accounts cannot access the organization-restricted folder.

### Sign in with a different Google account

```bash
gcloud auth revoke
```

Then rerun your figure with `--login --refresh`.

### Display every option

```bash
uv run usecase/scripts/glm5.3/Fig_1_Benchmark_Breakdown.py --help
```
