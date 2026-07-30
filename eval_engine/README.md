# eval_engine

The deterministic execution layer of `agent-benchmark`. It contains no LLM or agent loop.
Humans—and later `eval_agent`—invoke the same CLI.

Agent integration is independent from benchmarks. Agent profiles live under `config/agents/`,
while their invocation adapters live under `agents/`. A benchmark selects a default agent without
owning that agent's configuration or execution logic.

The execution VM is fixed and long-lived. An administrator provisions it once; operators run the
CLI locally and keep experiment inputs and retrieved results on their own machine.

## Currently supported configurations

The engine currently supports these benchmark profiles:

| CLI profile | Benchmark | Tasks | Default agent | Sampling | Grading |
|---|---|---:|---|---|---|
| `swebench-verified` | SWE-bench Verified | 500 | `mini-swe-agent` | `random`, `domain`, or full | Official hermetic SWE-bench grader |
| `terminal-bench-2.1` | Terminal-Bench 2.1 | 89 | `terminus-2` | `random`, `category`, or full | Per-task verifier run inline by Harbor |
| `aider-polyglot` | Aider Polyglot Benchmark | 225 | `aider` | `random`, `language`, or full | Tests run inline by the official Aider runner |

These agent profiles are available:

| CLI profile | Version | Notes |
|---|---:|---|
| `mini-swe-agent` | 2.4.5 | Receives its generated model configuration and a per-task cost limit |
| `terminus-2` | 2.0.0 | Harbor built-in agent; receives model, reasoning, and provider arguments directly |
| `aider` | 0.86.0 | Aider Coder agent loop used by the native Aider benchmark runner |

Benchmark and agent selection remain separate, and `--agent` overrides the benchmark default.
Compatibility is validated because an agent must support the harness selected internally by the
benchmark profile.

| Benchmark | `mini-swe-agent` | `terminus-2` | `aider` |
|---|---|---|---|
| `swebench-verified` | Supported (default) | Supported | Not supported |
| `terminal-bench-2.1` | Supported | Supported (default) | Not supported |
| `aider-polyglot` | Not supported | Not supported | Supported (default; officially comparable only for a complete official run) |

The configured model profiles—`glm-5.2`, `kimi-k3`, and `opus-5`—can be paired with the supported
SWE-bench and Terminal-Bench agent combinations. Kimi intentionally routes through OpenRouter to
MoonshotAI rather than Friendli. The pinned Aider stack has the Opus-specific limitation documented
below.

The harness is not a user-selectable combination axis and there is no `--harness` option. Each
benchmark profile selects its execution harness internally. SWE-bench and Terminal-Bench use
Harbor; Aider Polyglot uses the benchmark-owned `aider-native` harness.

Current validation status is narrower than the supported interface: SWE-bench with mini-swe-agent
and Terminal-Bench with Terminus 2 have completed real VM end-to-end runs. The two overridden-agent
combinations have resolved-spec and Harbor-command tests; run a small paid smoke test before a full
evaluation. Aider Polyglot has catalog, pool, command, transport, result, and mocked integration
coverage plus one-task real-VM smoke results for GLM and Kimi. These samples validate integration,
not benchmark quality or official leaderboard comparability.

| Benchmark | `glm-5.2` | `kimi-k3` | `opus-5` |
|---|---|---|---|
| `swebench-verified` | Supported; `high`, `xhigh` | Supported; `low`, `high`, `max` | Supported; `low`, `medium`, `high`, `xhigh`, `max` |
| `terminal-bench-2.1` | Supported; `high`, `xhigh` | Supported; `low`, `high`, `max` | Supported; `low`, `medium`, `high`, `xhigh`, `max` |
| `aider-polyglot` | Supported; `high` VM verified | Supported; `high` VM verified | Not supported; pinned effort translation is incompatible |

Effort values in the first two rows are accepted by the model profile and passed to the selected
agent. They do not claim that every benchmark-model-effort combination has completed a paid VM
run. The Aider GLM and Kimi smoke results additionally confirm that `high` was recorded by the
native runner.

The pinned Aider 0.86.0 image installs LiteLLM 1.75.0. Its non-OpenRouter effort path sends
`extra_body.reasoning_effort`, but Claude Opus 5 requires `output_config.effort`; Anthropic rejects
the former with `extra_body: Extra inputs are not permitted`. Do not run `opus-5` with
`aider-polyglot` until the native Aider/LiteLLM pin is upgraded or an officially validated
translation is added. Model-call errors with error output and zero tokens are classified as
`AiderModelCallError`, fail validation, and are never reported as ordinary zero-score results.

For Kimi, `--byok` is not accepted by the current profile because that CLI flag specifically means
Friendli BYOK with Friendli-only routing. OpenRouter may automatically use a separately registered
MoonshotAI BYOK key, but that requires workspace configuration and must be confirmed from
OpenRouter response metadata; the recorded smoke test used shared capacity.

For GLM, `--provider friendli --byok` verifies that the request is restricted to Friendli and that
provider fallback is disabled. OpenRouter selects registered BYOK keys at the workspace layer, but
the pinned Aider result does not retain OpenRouter's final `is_byok` routing metadata. Confirm
actual key use in the OpenRouter Activity raw metadata (`is_byok: true`) and enable the workspace's
always-use setting when shared capacity must never be used.

An Aider result is directly comparable with the official leaderboard only when all 225 pinned
tasks complete using the pinned native runner, dataset, Dockerfile, Aider Coder loop, and matching
model/edit-format settings. Sampled or incomplete runs are useful agent-benchmark evaluations but
are explicitly reported as not officially comparable.

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
automatically selects the extra required by the resolved benchmark profile. Aider Polyglot has no
additional local Python extra; its pinned dependencies and language toolchains live in the native
Docker image.

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
default. Model profiles do not select agents.

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

A three-task, language-balanced Aider Polyglot plan is:

```bash
uv run agent-bench plan \
  --benchmark aider-polyglot \
  --model glm-5.2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --byok \
  --workers 1 \
  --budget-usd 5 \
  --sampling language \
  --size 3
```

The default agent is `aider`; specifying either Harbor agent with `--agent` is rejected before
deployment.

### 8. Run a one-task smoke experiment

This command makes a real model request and may incur cost:

```bash
uv run agent-bench run \
  --benchmark swebench-verified \
  --agent mini-swe-agent \
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
| `opus-5` | `max` | `anthropic` | `ANTHROPIC_API_KEY`; not currently usable with `aider-polyglot` |

Run one experiment at a time. The engine holds a VM-wide lease until completion or cancellation.

## Run management

Every run prints an ID and stores its local bundle under `runs/<run-id>/`.

```bash
uv run agent-bench status RUN_ID
uv run agent-bench active
uv run agent-bench cancel RUN_ID
uv run agent-bench resume RUN_ID
uv run agent-bench report RUN_ID
```

`active` queries the fixed VM's exclusive lease and prints its run ID, or `no active run` when the
VM is idle. Use `status RUN_ID` to inspect that run's durable local stage state.

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
Aider Polyglot accepts deterministic seeded `random` and language-balanced `language`; omitting
sampling selects all 225 pinned tasks. The dataset has no benchmark-defined category taxonomy, so
category sampling is not exposed. Task IDs retain their language prefix, allowing explicit pool
inspection and reproducible reruns.

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

For Terminal-Bench, `execute` invokes Harbor once; Harbor runs the selected agent and the task's
hidden verifier inline. `grade` only validates Harbor's existing reward artifacts and never reruns
the verifier. Rewards are binary for the pinned 2.1 dataset and reports include accuracy and mean
reward. An interrupted execute stage resumes the deterministic Harbor job and skips completed
trials.

For Aider Polyglot, `prepare` checks out the pinned Aider and exercise revisions, verifies the
official Dockerfile hash, builds the native image, and materializes the run pool. `execute` invokes
the official runner, which performs both Aider generation and tests for two attempts in one native
operation. `grade` validates those existing artifacts and does not rerun tests. `normalize`
preserves pass-at-1, pass-at-2, edit-format, token, and cost data where supplied. Reports show both
the official completed-task denominator and the stricter selected-pool denominator; missing cost
data is not silently treated as zero. Resume keeps valid terminal result artifacts and reruns only
missing or malformed tasks.

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
- For Aider Polyglot, outbound GitHub access during initial checkout/image build and model-provider
  access during execution

The pinned Terminal-Bench 2.1 tasks request up to 4 CPUs, 8 GiB RAM, and 10 GiB task storage; allow
additional disk for Harbor caches and container images. A full run can take many hours and incur
substantial model cost, so validate with `plan` and obtain approval before a paid smoke run.

The official Aider image installs C++, Go, Java, JavaScript, Python, and Rust toolchains. The native
container is capped at 12 GiB, so provision at least 16 GiB host RAM plus image and repository disk;
the first build downloads toolchains and is materially slower than cached runs. The supported
execution target is Linux Docker. Apple Silicon hosts may build a different architecture and are
not treated as leaderboard-comparable without separate validation.

The Docker group grants control of the Docker daemon and is effectively root-level access. Add
only a trusted benchmark execution account.

Prefer one dedicated Linux account, such as `agentbench`, for a shared VM. Give approved users SSH
access to that account with their individual public keys. This keeps filesystem ownership, Docker
permissions, and caches consistent. If operators use separate Linux accounts, each needs Docker
access and separate run/cache ownership management.

Then connect to the VM:

```bash
gcloud compute ssh <VM_NAME> --zone <ZONE>
```

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
