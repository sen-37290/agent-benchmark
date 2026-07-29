# Execution VM provisioning

Provision each long-lived execution VM once before running `agent-bench`. The engine deploys its
source and creates its locked Python environment automatically, but it deliberately does not
modify the VM operating system during an experiment.

## Requirements

- Linux VM reachable through standard SSH
- `python3`, `uv`, `docker`, Docker Compose v2, and `rsync` available in non-interactive SSH
  sessions
- Docker daemon enabled and running
- Benchmark SSH user allowed to access Docker without `sudo`
- Enough disk for benchmark repositories, datasets, and Docker images

The Docker group grants control of the Docker daemon and is effectively root-level access. Add
only a trusted benchmark execution account.

Prefer one dedicated Linux execution account, such as `agentbench`, for a shared fixed VM. Grant
approved users SSH access to that account with their individual public keys. This keeps the
runtime user, filesystem ownership, Docker permissions, and caches consistent across operators.
If operators instead use separate Linux accounts, each account needs Docker access and separate
run/cache ownership must be handled by the administrator.

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

# Harbor invokes `docker compose`. Install the Compose CLI plugin system-wide.
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

Disconnect and reconnect after changing Docker group membership. Then verify:

```bash
python3 --version
uv --version
rsync --version
docker info
docker compose version
docker run --rm hello-world
```

## Granting user access

Provisioning the VM does not grant users SSH access. Add each approved user's public key to the
dedicated execution account using the organization's normal SSH, OS Login, or access-management
process. Never share private keys or store them in this repository.

The administrator should give the user the SSH target (`user@host` or an approved SSH alias) and
confirm that `ssh <target>` works. The remaining user workflow is documented in
[GETTING_STARTED.md](GETTING_STARTED.md).
