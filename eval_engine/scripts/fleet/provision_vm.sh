#!/usr/bin/env bash
# Provision one execution VM. Idempotent: safe to re-run on an already-prepared host.
#
# Run this ON the VM (deploy_experiment.sh does it for you):
#     bash provision_vm.sh <base|cybergym>
#
# Everything here was learned the hard way on a fresh Debian 12 box, where `agent-bench prepare`
# assumes a toolchain that is not present. The two non-obvious requirements both fail *in
# sequence* partway through prepare, after a long download:
#   * without g++ (build-essential) the OpenHands venv build dies compiling the pylcs C++ ext
#   * Debian's apt `nodejs` is v18, and OpenHands `make build` requires Node >= 22
set -euo pipefail

PROFILE="${1:-base}"
NODE_MAJOR=22
# Matches the plugin already installed on the original working VM.
COMPOSE_VERSION=5.1.4
POETRY_VERSION=2.4.1
PYTHON_VERSION=3.12

log() { printf '\n=== %s\n' "$*"; }

log "profile: ${PROFILE} on $(hostname), $(nproc) cpus, $(df -h / | awk 'NR==2{print $4}') free"

log "base packages"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  docker.io git rsync curl ca-certificates build-essential pkg-config jq screen

log "docker"
sudo systemctl enable --now docker
# The docker group only takes effect in a NEW login session, which the deploy step's fresh ssh
# connection provides.
sudo usermod -aG docker "$USER"

log "docker compose v2 plugin"
# Debian's docker.io package ships Docker Engine WITHOUT the Compose v2 plugin, so `docker
# compose` does not exist and Harbor fails every task at environment startup with
# "unknown flag: --project-name" (return code 125). Install the plugin binary the same way the
# original working VM has it, pinned to the same version.
COMPOSE_PLUGIN_DIR=/usr/local/lib/docker/cli-plugins
if ! docker compose version >/dev/null 2>&1; then
  sudo mkdir -p "$COMPOSE_PLUGIN_DIR"
  sudo curl -fsSL -o "$COMPOSE_PLUGIN_DIR/docker-compose" \
    "https://github.com/docker/compose/releases/download/v${COMPOSE_VERSION}/docker-compose-linux-x86_64"
  sudo chmod +x "$COMPOSE_PLUGIN_DIR/docker-compose"
fi
docker compose version

log "uv"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"
sudo ln -sf "$UV_BIN" /usr/local/bin/uv

if [ "$PROFILE" = "cybergym" ]; then
  log "cybergym extras: 7z, node ${NODE_MAJOR}, poetry ${POETRY_VERSION}, python ${PYTHON_VERSION}"
  sudo apt-get install -y -qq p7zip-full
  # NodeSource, not apt: apt gives v18 and OpenHands' `make build` requires >= 22.
  if ! command -v node >/dev/null || [ "$(node -v | cut -c2- | cut -d. -f1)" -lt "$NODE_MAJOR" ]; then
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | sudo -E bash -
    sudo apt-get install -y -qq nodejs
  fi
  uv tool install "poetry==${POETRY_VERSION}" --force
  sudo ln -sf "$HOME/.local/bin/poetry" /usr/local/bin/poetry
  uv python install "${PYTHON_VERSION}"
  sudo ln -sf "$(uv python find ${PYTHON_VERSION})" "/usr/local/bin/python${PYTHON_VERSION}"
fi

log "self-ssh"
# The execution backend is SSH-only and has no local mode, so a VM that drives itself must be
# able to ssh to localhost without a password. This is what lets the controller live on the same
# VM as the executor, which is what lets the laptop be closed.
if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
  ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -q
fi
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if ! grep -qFf "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  cat "$HOME/.ssh/id_ed25519.pub" >> "$HOME/.ssh/authorized_keys"
fi
chmod 600 "$HOME/.ssh/authorized_keys"
ssh-keygen -F localhost >/dev/null 2>&1 || ssh-keyscan -t ed25519 localhost >> "$HOME/.ssh/known_hosts" 2>/dev/null

log "verification"
ssh -o BatchMode=yes -o StrictHostKeyChecking=no localhost true && echo "self-ssh: OK"
for tool in docker git rsync uv jq; do
  printf '%-8s %s\n' "$tool" "$(command -v "$tool" || echo MISSING)"
done
if [ "$PROFILE" = "cybergym" ]; then
  for tool in node poetry "python${PYTHON_VERSION}" 7z; do
    printf '%-8s %s\n' "$tool" "$(command -v "$tool" || echo MISSING)"
  done
  node -v
fi
# Confirm the docker group is actually usable in a fresh session, not just assigned.
sg docker -c 'docker run --rm hello-world >/dev/null 2>&1' && echo "docker run: OK" \
  || echo "docker run: NOT YET (log out and back in, then re-check)"
# Harbor drives `docker compose`, so a working engine alone is not enough.
sg docker -c 'docker compose version >/dev/null 2>&1' && echo "docker compose: OK" \
  || echo "docker compose: MISSING (Harbor tasks would all fail at environment startup)"

log "provisioned"
