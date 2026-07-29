# Execution VM provisioning

Provision each long-lived execution VM once before running `agent-bench`. The engine deploys its
source and creates its locked Python environment automatically, but it deliberately does not
modify the VM operating system during an experiment.

## Requirements

- Linux VM reachable through standard SSH
- `python3`, `uv`, `docker`, and `rsync` available in non-interactive SSH sessions
- Docker daemon enabled and running
- Benchmark SSH user allowed to access Docker without `sudo`
- Enough disk for benchmark repositories, datasets, and Docker images

The Docker group grants control of the Docker daemon and is effectively root-level access. Add
only a trusted benchmark execution account.

## Debian 12 setup

Run these commands once on the VM as the benchmark SSH user:

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
```

Disconnect and reconnect after changing Docker group membership. Then verify:

```bash
python3 --version
uv --version
rsync --version
docker info
docker run --rm hello-world
```

## User setup after cloning

Every user still needs SSH authorization for the VM. Keep private keys in `~/.ssh`, an SSH agent,
or an organization-managed credential system; do not store them in `.env` or this repository.

```bash
cd eval_engine
cp .env.example .env
```

Fill in `.env`, then run the non-mutating preflight:

```bash
uv sync --extra swebench
uv run agent-bench remote doctor
```

Once the preflight passes, future runs automatically upload the source and run bundle, synchronize
the locked Python environment, execute Harbor, retrieve and verify artifacts, and retain shared VM
caches for later experiments.
