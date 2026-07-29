# Getting started

This guide takes a new operator from repository access to a one-task SWE-bench Verified run. The
execution VM is fixed and long-lived; an administrator provisions it once, while each operator
keeps experiment inputs and retrieved results locally.

## 1. Request access

Obtain the following before cloning:

- Read access to the GitHub repository
- SSH access to the provisioned execution VM
- The VM SSH target as `user@host`, or an SSH alias and its configuration
- An OpenRouter API key for GLM-5.2 or Kimi-K3
- An Anthropic API key for Opus 5
- For Friendli BYOK, a Friendli key already registered in the OpenRouter workspace dashboard

The VM administrator follows [PROVISIONING.md](PROVISIONING.md). An operator does not install
Docker or system packages on the VM. SSH private keys belong in `~/.ssh`, an SSH agent, or an
organization-managed credential system—not in `.env`.

## 2. Install local prerequisites

The operator machine needs:

- Git
- OpenSSH client
- `rsync`
- `uv`

Confirm they are available:

```bash
git --version
ssh -V
rsync --version
uv --version
```

The operator machine does not need Docker; benchmark containers run on the VM.

## 3. Verify SSH access

If the administrator provides a direct target, verify it as given:

```bash
ssh agentbench@VM_HOST
```

Alternatively, create a local SSH alias:

```sshconfig
Host agent-benchmark-vm
    HostName VM_HOST
    User agentbench
    IdentityFile ~/.ssh/ORGANIZATION_KEY
    IdentitiesOnly yes
```

Then verify:

```bash
ssh agent-benchmark-vm
```

Resolve SSH or host-key errors before running the engine. `agent-bench` uses the same local `ssh`
and `rsync` configuration.

## 4. Clone and install

```bash
git clone https://github.com/sen-37290/agent-benchmark.git
cd agent-benchmark/eval_engine
uv sync --extra swebench
```

`uv` creates the local environment from `uv.lock`. It also installs the Python version required by
the project when necessary.

## 5. Configure `.env`

```bash
cp .env.example .env
```

Fill in the SSH target and only the API keys needed for the intended models:

```dotenv
AGENT_BENCH_SSH_HOST=agent-benchmark-vm
OPENROUTER_API_KEY=
ANTHROPIC_API_KEY=
```

The CLI loads `eval_engine/.env` automatically. Existing process environment variables override
the file, which allows CI or a secret manager to inject credentials. `.env` is ignored by Git.

Do not add `FRIENDLI_API_KEY`. OpenRouter ignores an upstream Friendli key sent with an inference
request. Friendli BYOK uses the key registered in the OpenRouter workspace dashboard.

## 6. Run the preflight

```bash
uv run agent-bench remote doctor
```

A successful result ends with:

```text
remote preflight passed
```

This checks SSH connectivity and the remote availability of `python3`, `uv`, `docker`, Docker
Compose, and `rsync`. It does not call a model or start a benchmark.

## 7. Validate an experiment plan

```bash
uv run agent-bench plan \
  --benchmark swebench-verified \
  --model glm-5.2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --byok \
  --workers 1 \
  --budget-usd 5 \
  --sampling random \
  --size 1
```

`plan` validates and prints the resolved spec without using the VM or calling the model. For the
Friendli BYOK case, verify that it contains `api: openrouter`, `provider.only: [friendli]`, and
`allow_fallbacks: false`.

## 8. Run a one-task smoke experiment

The following command makes a real model request and may incur cost:

```bash
uv run agent-bench run \
  --benchmark swebench-verified \
  --model glm-5.2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --byok \
  --workers 1 \
  --budget-usd 5 \
  --per-task-cost-limit-usd 5 \
  --sampling random \
  --size 1
```

Always use an explicit one-task sample for the first run. Omitting `--sampling` and `--size` runs
the full 500-task SWE-bench Verified benchmark.

Equivalent model/provider selections are:

| Model profile | Effort example | Provider | Required key |
|---|---|---|---|
| `glm-5.2` | `xhigh` | `friendli --byok` | `OPENROUTER_API_KEY` |
| `kimi-k3` | `max` | `openrouter` | `OPENROUTER_API_KEY` |
| `opus-5` | `max` | `anthropic` | `ANTHROPIC_API_KEY` |

Run only one experiment at a time on the fixed VM. The engine holds a VM-wide lease until a run
finishes cleanup.

## 9. Inspect and resume

Each run prints a run ID and stores its complete local bundle under `runs/<run-id>/`.

```bash
uv run agent-bench status RUN_ID
uv run agent-bench cancel RUN_ID
uv run agent-bench resume RUN_ID
uv run agent-bench report RUN_ID
```

The bundle contains the original request, resolved spec, stage state, event log, copied pool,
retrieved raw artifacts, normalized results, and report. Re-running `resume` continues from the
first incomplete stage.

`cancel` stops processes and Docker resources belonging to that exact run, removes its remote
workspace, releases its VM lease, and records the cancellation locally. A cancelled run cannot be
resumed; start a new run instead.

## Common failures

- `Permission denied (publickey)`: SSH access or the selected key is not configured.
- `Host key verification failed`: verify the host with the administrator and fix local
  `known_hosts` or SSH alias configuration.
- `set AGENT_BENCH_SSH_HOST`: `.env` is missing, misplaced, or incomplete.
- `docker: permission denied`: the remote execution account lacks Docker daemon access; contact the
  VM administrator.
- `VM is leased by run`: another run is active, or a failed run still owns the lease. Resume or
  inspect that run before starting another.
- Missing API-key error: fill in the key required by the selected model profile.
