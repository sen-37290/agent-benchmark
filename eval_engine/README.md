# eval_engine

The deterministic execution layer of `agent-benchmark`. It contains no LLM or agent loop.
Humans—and later `eval_agent`—invoke the same CLI.

Subject-agent integration is independent from benchmarks and harnesses. Agent profiles live under
`config/agents/`, while their invocation adapters live under `agents/`. A benchmark may
select a default agent profile without owning that agent's configuration or execution logic.

The execution VM is fixed and long-lived. An administrator provisions it once; operators run the
CLI locally and keep experiment inputs and retrieved results on their own machine.

## Getting started

### 1. Request access

Obtain:

- Read access to this repository
- SSH access to the provisioned execution VM
- The VM target as `user@host`, or an approved SSH alias
- An OpenRouter API key for GLM-5.2 or Kimi-K3
- An Anthropic API key for Opus 5
- For Friendli BYOK, a Friendli key registered in the OpenRouter workspace dashboard

SSH private keys belong in `~/.ssh`, an SSH agent, or an organization-managed credential system—not
in `.env`.

### 2. Install local prerequisites

The operator machine needs Git, OpenSSH, `rsync`, and `uv`. It does not need Docker.

```bash
git --version
ssh -V
rsync --version
uv --version
```

### 3. Verify SSH access

Use the direct target supplied by the administrator:

```bash
ssh agentbench@VM_HOST
```

Or create a local SSH alias:

```sshconfig
Host agent-benchmark-vm
    HostName VM_HOST
    User agentbench
    IdentityFile ~/.ssh/ORGANIZATION_KEY
    IdentitiesOnly yes
```

Then verify it:

```bash
ssh agent-benchmark-vm
```

Resolve SSH and host-key errors before running the engine. `agent-bench` uses the same local `ssh`
and `rsync` configuration.

### 4. Clone and install

```bash
git clone https://github.com/sen-37290/agent-benchmark.git
cd agent-benchmark/eval_engine
uv sync --extra swebench
```

For Terminal-Bench-only development, use `uv sync --extra terminalbench`. Remote deployment
automatically selects the extra required by the resolved benchmark profile.

`uv` creates the environment from `uv.lock` and installs the required Python version when needed.

### 5. Configure `.env`

```bash
cp .env.example .env
```

Fill in the SSH target and only the API keys required by the intended models:

```dotenv
AGENT_BENCH_SSH_HOST=agent-benchmark-vm
OPENROUTER_API_KEY=
ANTHROPIC_API_KEY=
```

The CLI loads `eval_engine/.env` automatically. Existing process environment variables take
precedence, allowing CI or a secret manager to inject credentials. `.env` is ignored by Git.

Do not add `FRIENDLI_API_KEY`. OpenRouter ignores a Friendli key sent with an inference request;
Friendli BYOK uses the key registered in the OpenRouter workspace dashboard.

### 6. Run the preflight

```bash
uv run agent-bench remote doctor
```

A successful preflight ends with `remote preflight passed`. It checks SSH and the remote
availability of `python3`, `uv`, `docker`, Docker Compose, and `rsync`, without calling a model or
starting a benchmark.

### 7. Validate a plan

```bash
uv run agent-bench plan \
  --benchmark swebench-verified \
  --model glm-5.2 \
  --agent terminus-2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --byok \
  --workers 1 \
  --budget-usd 5 \
  --sampling random \
  --size 1
```

`plan` validates and prints the resolved spec without using the VM or calling the model. For
Friendli BYOK, verify `api: openrouter`, `provider.only: [friendli]`, and
`allow_fallbacks: false`. `--agent` is optional: an explicit CLI value overrides the benchmark
default, which in turn overrides the model default.

Terminal-Bench 2.1 can be planned by changing the benchmark and sampling strategy:

```bash
uv run agent-bench plan \
  --benchmark terminal-bench-2.1 \
  --model glm-5.2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --byok \
  --workers 1 \
  --budget-usd 5 \
  --sampling category \
  --size 1
```

### 8. Run a one-task smoke experiment

This command makes a real model request and may incur cost:

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
all 500 SWE-bench Verified tasks.

| Model profile | Effort example | Provider | Required key |
|---|---|---|---|
| `glm-5.2` | `xhigh` | `friendli --byok` | `OPENROUTER_API_KEY` |
| `kimi-k3` | `max` | `openrouter` | `OPENROUTER_API_KEY` |
| `opus-5` | `max` | `anthropic` | `ANTHROPIC_API_KEY` |

Run one experiment at a time. The engine holds a VM-wide lease until completion or cancellation.

## Run management

Every run prints an ID and stores its local bundle under `runs/<run-id>/`.

```bash
uv run agent-bench status RUN_ID
uv run agent-bench cancel RUN_ID
uv run agent-bench resume RUN_ID
uv run agent-bench report RUN_ID
```

The bundle contains the request, immutable resolved spec, stage state, event log, copied pool, raw
artifacts, normalized results, and report. `resume` continues from the first incomplete stage.

`cancel` stops processes and Docker resources belonging to the exact run, removes its remote
workspace, releases its lease, and records cancellation locally. A cancelled run cannot resume;
start a new run instead.

## Sampling and providers

Sampling is optional. Omitting both `--sampling` and `--size` creates a full run-specific pool.
SWE-bench Verified accepts `random` and `domain`. Generated pools are ignored by Git and copied
into the permanent run bundle as `inputs/pool.json`.
Terminal-Bench 2.1 accepts `random` and category-balanced `category`; omitting sampling selects
all 89 pinned tasks.

| `--provider` | Internal transport | Routing |
|---|---|---|
| `openrouter` | OpenRouter | OpenRouter default routing |
| `friendli` | OpenRouter | `provider.only=[friendli]` |
| `anthropic` | Anthropic via litellm | Anthropic first-party API |

Add `--byok` to the Friendli form to disable OpenRouter fallback. The selected provider, internal
transport, and route are recorded separately in `resolved.yaml`.

## Run lifecycle

```text
deploy → prepare → execute → grade → collect → normalize → report → cleanup
```

The remote workspace is deleted only after artifact checksums have been verified locally. Shared
Docker, dataset, and dependency caches remain on the VM for later experiments.

For Terminal-Bench, `execute` invokes Harbor once; Harbor runs the subject agent and the task's
hidden verifier inline. `grade` only validates Harbor's existing reward artifacts and never reruns
the verifier. Rewards are binary for the pinned 2.1 dataset and reports include accuracy and mean
reward. An interrupted execute stage resumes the deterministic Harbor job and skips completed
trials.

Terminal-Bench 2.1 defaults to Harbor's built-in Terminus 2 (`terminus-2`, version 2.0.0), while
SWE-bench Verified defaults to mini-swe-agent. Either benchmark can use another configured agent
with `--agent`; benchmark preparation and grading do not change. Terminus 2 receives the resolved
reasoning effort and OpenRouter provider routing directly through Harbor. The engine-wide measured
cost watchdog still applies, but Terminus 2 does not expose mini-swe-agent's per-task cost-limit
argument.

## Execution VM provisioning

Provision each long-lived VM once. The engine deploys its source and creates its locked Python
environment automatically, but deliberately does not modify the VM operating system during an
experiment.

### VM requirements

- Linux reachable through standard SSH
- `python3`, `uv`, `docker`, Docker Compose v2, and `rsync` in non-interactive SSH sessions
- Docker daemon enabled and running
- Benchmark SSH account allowed to access Docker without `sudo`
- Enough disk for repositories, datasets, and Docker images
- Outbound access to Harbor Hub and the container registries used by Terminal-Bench tasks

The pinned Terminal-Bench 2.1 tasks request up to 4 CPUs, 8 GiB RAM, and 10 GiB task storage; allow
additional disk for Harbor caches and container images. A full run can take many hours and incur
substantial model cost, so validate with `plan` and obtain approval before a paid smoke run.

The Docker group grants control of the Docker daemon and is effectively root-level access. Add
only a trusted benchmark execution account.

Prefer one dedicated Linux account, such as `agentbench`, for a shared VM. Give approved users SSH
access to that account with their individual public keys. This keeps filesystem ownership, Docker
permissions, and caches consistent. If operators use separate Linux accounts, each needs Docker
access and separate run/cache ownership management.

### Debian 12 setup

Run once on the VM as the benchmark SSH account:

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl docker.io rsync

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

curl -LsSf https://astral.sh/uv/install.sh -o /tmp/agent-benchmark-uv-install.sh
sh /tmp/agent-benchmark-uv-install.sh
rm /tmp/agent-benchmark-uv-install.sh

# Non-interactive SSH does not necessarily load ~/.profile.
sudo ln -sfn "$HOME/.local/bin/uv" /usr/local/bin/uv
sudo ln -sfn "$HOME/.local/bin/uvx" /usr/local/bin/uvx

# Harbor invokes `docker compose`; install the Compose CLI plugin system-wide.
curl -fL \
  https://github.com/docker/compose/releases/download/v5.1.4/docker-compose-linux-x86_64 \
  -o /tmp/docker-compose-linux-x86_64
curl -fL \
  https://github.com/docker/compose/releases/download/v5.1.4/docker-compose-linux-x86_64.sha256 \
  -o /tmp/docker-compose-linux-x86_64.sha256
(cd /tmp && sha256sum -c docker-compose-linux-x86_64.sha256)
sudo install -D -m 0755 /tmp/docker-compose-linux-x86_64 \
  /usr/local/lib/docker/cli-plugins/docker-compose
rm /tmp/docker-compose-linux-x86_64 /tmp/docker-compose-linux-x86_64.sha256
```

Disconnect and reconnect after changing Docker group membership, then verify:

```bash
python3 --version
uv --version
rsync --version
docker info
docker compose version
docker run --rm hello-world
```

Provisioning does not grant SSH access. Add each approved user's public key to the dedicated
execution account through the organization's SSH, OS Login, or access-management process. Never
share private keys or store them in this repository.

## Common failures

- `Permission denied (publickey)`: SSH access or the selected key is not configured.
- `Host key verification failed`: verify the host and fix local `known_hosts` or SSH alias config.
- `set AGENT_BENCH_SSH_HOST`: `.env` is missing, misplaced, or incomplete.
- `docker: permission denied`: the remote execution account lacks Docker daemon access.
- `VM is leased by run`: another run is active or a failed run still owns the lease. Inspect,
  resume, or cancel that run before starting another.
- Missing API-key error: fill in the key required by the selected model profile.
