# Manual fixed-VM smoke test

The commands in this guide are for the operator to run manually. The implementation and its test
suite do not connect to the VM or call a model API.

## 1. Prepare the VM once

The fixed Linux VM must have:

- SSH access from the local machine
- Python 3.13
- `uv`
- Docker with permission for the SSH user to run it
- `rsync`
- enough disk for SWE-bench Docker images

The engine does not create, stop, or delete the VM.

## 2. Prepare the local shell

```bash
cd ~/Documents/agent-benchmark/eval_engine
uv sync --extra swebench

export AGENT_BENCH_SSH_HOST=<ssh-host-or-config-alias>
export OPENROUTER_API_KEY=<dedicated-key>
```

Use a dedicated, capped, revoke-after-use API key. Do not pass the key as a CLI argument.

For an Anthropic profile, set `ANTHROPIC_API_KEY` instead. OpenRouter BYOK additionally requires
`--provider-route <provider-slug> --byok`; note that provider-reported `$0` costs cannot enforce the
engine's aggregate cost watchdog, so configure the cap with that provider first.

## 3. Run read-only preflight

```bash
uv run agent-bench profiles list
uv run agent-bench remote doctor --target fixed-vm

uv run agent-bench plan \
  --benchmark swebench-verified \
  --model glm-5.2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --workers 2 \
  --budget-usd 10 \
  --sampling random \
  --size 2
```

`plan` reads the benchmark dataset locally, creates a temporary pool, and prints the resolved spec.
It creates no run, contacts no VM, and spends no model API credit. Confirm the model class, model
ID, effort path, pool hash, adapter/grader versions, target, and budget before continuing.

## 4. Run the two-task integration gate

This step performs real model calls and official Docker grading:

```bash
uv run agent-bench run \
  --benchmark swebench-verified \
  --model glm-5.2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --workers 2 \
  --budget-usd 10 \
  --per-task-cost-limit-usd 5 \
  --sampling random \
  --size 2
```

The CLI prints `created: <run-id>` before remote work begins. Save that ID.

Omitting both `--sampling` and `--size` means the full 500-task benchmark. SWE-bench also supports
`--sampling domain --size <n>` for a domain-balanced sample. Add `--byok` when the Friendli route
uses a key registered through OpenRouter BYOK.

## 5. Inspect or resume

```bash
uv run agent-bench status <run-id>
uv run agent-bench resume <run-id>
```

On failure, the remote workspace is retained. Fix the cause and use `resume`; completed stages are
not repeated. Do not manually remove the VM lease unless the recorded run is unrecoverable and its
workspace has been inspected.

## 6. Verify local results

After success, inspect:

```text
runs/<run-id>/request.yaml
runs/<run-id>/resolved.yaml
runs/<run-id>/inputs/pool.json
runs/<run-id>/state.json
runs/<run-id>/events.jsonl
runs/<run-id>/artifacts/
runs/<run-id>/results/task_results.jsonl
runs/<run-id>/results/summary.csv
runs/<run-id>/report/summary.json
runs/<run-id>/report/report.md
```

Expected gate conditions:

- two task records exist
- official grading produced `official_summary.json`
- each completed trial has a captured `model_patch.diff`
- `collect`, `normalize`, `report`, and `cleanup` are `succeeded`
- no API key value appears in request or resolved specs

Regenerate the report without using the VM:

```bash
uv run agent-bench report <run-id>
```

## 7. Confirm remote cleanup

Successful cleanup removes the run-specific remote directory and its secret file. It retains shared
cache directories. Replace `<run-id>` below with the saved ID:

```bash
ssh "$AGENT_BENCH_SSH_HOST" \
  "test ! -e /tmp/agent-benchmark/runs/<run-id> && echo 'run workspace removed'"
```

The following cache root may remain and is reused:

```text
/tmp/agent-benchmark/cache/
```
