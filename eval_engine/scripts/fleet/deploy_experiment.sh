#!/usr/bin/env bash
# Deploy and start one experiment on its VM. Run this from the laptop; once it returns, the VM is
# self-driving and the laptop can be closed.
#
#     ./deploy_experiment.sh <label> [--provision] [--no-start]
#
# The controller runs ON the execution VM, not on the laptop. The engine's backend is an SSH
# driver, so co-locating it with the executor (via self-ssh to localhost) costs nothing and makes
# the run independent of the laptop's network and lid. Results stay on the VM: nothing is ever
# pulled to local.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
EXPERIMENTS="$HERE/experiments.yaml"

LABEL="${1:-}"
shift || true
PROVISION=0
START=1
for arg in "$@"; do
  case "$arg" in
    --provision) PROVISION=1 ;;
    --no-start) START=0 ;;
    *) echo "unknown option: $arg" >&2; exit 64 ;;
  esac
done

if [ -z "$LABEL" ]; then
  echo "usage: $0 <label> [--provision] [--no-start]" >&2
  echo "labels:" >&2
  "$HERE/fleet.py" labels >&2
  exit 64
fi

# Read this experiment's row once, as shell assignments.
eval "$("$HERE/fleet.py" env "$LABEL")"
: "${FLEET_VM:?unknown label $LABEL}"

SSH_TARGET="${FLEET_SSH_USER}@${FLEET_VM_IP}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o ServerAliveInterval=15)
if [ -n "${FLEET_SSH_KEY:-}" ]; then
  SSH_OPTS+=(-i "$FLEET_SSH_KEY")
fi

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "$LABEL -> $FLEET_VM ($FLEET_VM_IP)"

if [ "$PROVISION" = "1" ]; then
  say "provisioning"
  # CyberGym needs the heavier toolchain (7z, Node 22, Poetry, Python 3.12).
  profile=base
  case "$FLEET_BENCHMARK" in cybergym*) profile=cybergym ;; esac
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "mkdir -p '$FLEET_REMOTE_ROOT'"
  scp "${SSH_OPTS[@]}" "$HERE/provision_vm.sh" "$SSH_TARGET:/tmp/provision_vm.sh" >/dev/null
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "bash /tmp/provision_vm.sh $profile"
fi

say "syncing the repo"
# The working branch is local-only, so `git clone` on the VM is not an option: transfer the tree
# directly. `.env` and `.agent-bench/targets.local.yaml` are untracked but required, so they must
# ride along. An ABSOLUTE destination is required -- a relative one has silently landed nothing.
rsync -az --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude 'eval_engine/runs' --exclude 'eval_engine/pools' --exclude 'eval_engine/dist' \
  --exclude 'results' --exclude 'usecase' --exclude '.DS_Store' \
  -e "ssh ${SSH_OPTS[*]}" \
  "$REPO_ROOT/" "$SSH_TARGET:$FLEET_REMOTE_ROOT/"

say "installing dependencies"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "cd '$FLEET_REMOTE_ROOT/eval_engine' && uv sync --frozen --extra ${FLEET_DEPENDENCY_EXTRA}"

say "writing launch.env"
# 0600, and run_experiment.sh deletes it as soon as it has been sourced, so the key does not sit
# on disk for the life of the run. It is piped over stdin so it never appears in a process list.
API_KEY_VALUE="$(cd "$REPO_ROOT" && python3 - "$FLEET_API_KEY_FROM" <<'PY'
import sys
name = sys.argv[1]
for line in open(".env"):
    line = line.strip()
    if line.startswith(f"{name}="):
        print(line.split("=", 1)[1].strip().strip('"').strip("'"))
        break
PY
)"
if [ -z "$API_KEY_VALUE" ]; then
  echo "FATAL: $FLEET_API_KEY_FROM has no value in $REPO_ROOT/.env" >&2
  exit 78
fi

ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
  "mkdir -p '$FLEET_REMOTE_ROOT/.fleet' && umask 077 && cat > '$FLEET_REMOTE_ROOT/.fleet/launch.env'" <<EOF
LABEL=$LABEL
BENCHMARK=$FLEET_BENCHMARK
MODEL=$FLEET_MODEL
PROVIDER=$FLEET_PROVIDER
API_KEY_FROM=$FLEET_API_KEY_FROM
EXPERIMENT_CAP_USD=$FLEET_EXPERIMENT_CAP_USD
PER_TASK_CAP_USD=${FLEET_PER_TASK_CAP_USD:-}
WORKERS=$FLEET_WORKERS
CONTROLLER_DIR=$FLEET_REMOTE_ROOT
AGENT_BENCH_SSH_HOST=localhost
$FLEET_API_KEY_FROM=$API_KEY_VALUE
EOF
unset API_KEY_VALUE

say "installing systemd unit agent-bench@$LABEL"
# systemd rather than the previous ad-hoc `screen`: it survives a VM reboot, gives journalctl, and
# cannot be lost to a stray `screen -wipe`. TimeoutStopSec is generous because SIGTERM triggers a
# cooperative drain -- in-flight tasks are allowed to finish and be graded, never killed.
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "sudo tee /etc/systemd/system/agent-bench@.service >/dev/null" <<EOF
[Unit]
Description=agent-bench experiment %i
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=$FLEET_SSH_USER
WorkingDirectory=$FLEET_REMOTE_ROOT/eval_engine
Environment=HOME=/home/$FLEET_SSH_USER
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/home/$FLEET_SSH_USER/.local/bin
Environment=CONTROLLER_DIR=$FLEET_REMOTE_ROOT
ExecStart=/bin/bash $FLEET_REMOTE_ROOT/eval_engine/scripts/fleet/run_experiment.sh
Restart=no
KillSignal=SIGTERM
TimeoutStopSec=3600
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "sudo systemctl daemon-reload"

if [ "$START" = "1" ]; then
  say "starting"
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "sudo systemctl start 'agent-bench@$LABEL'"
  sleep 3
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "systemctl is-active 'agent-bench@$LABEL' || true"
  cat <<EOF

started. The VM is now self-driving; you can close the laptop.

  monitor:  $HERE/fleet.py watch
  logs:     $HERE/fleet.py logs $LABEL -f
  shell:    $HERE/fleet.py ssh $LABEL
  stop:     $HERE/fleet.py stop $LABEL     (drains cleanly, then grades)
EOF
else
  say "deployed but not started"
  echo "start it with: ssh $SSH_TARGET \"sudo systemctl start 'agent-bench@$LABEL'\""
fi
