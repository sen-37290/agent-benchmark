#!/usr/bin/env bash
# The end-to-end experiment, run ON its own VM under systemd. Never invoked by hand.
#
#   prepare -> generate -> record -> grade -> report
#
# The whole point of this script is the exit trap: however the run ends -- finished, stopped at
# its cost cap, crashed, or `systemctl stop` -- `agent-bench finalize` still grades and reports
# whatever completed, and nothing is ever cleaned up or copied to a laptop.
#
# Configuration arrives through launch.env, written by deploy_experiment.sh and deleted the moment
# it has been sourced so the API key does not sit on disk for the life of the run.
set -uo pipefail

CONTROLLER_DIR="${CONTROLLER_DIR:-$HOME/agent-benchmark}"
ENGINE_DIR="$CONTROLLER_DIR/eval_engine"
STATE_DIR="$CONTROLLER_DIR/.fleet"
mkdir -p "$STATE_DIR"

if [ -f "$STATE_DIR/launch.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$STATE_DIR/launch.env"
  set +a
  rm -f "$STATE_DIR/launch.env"
fi

: "${LABEL:?LABEL is required}"
: "${BENCHMARK:?BENCHMARK is required}"
: "${MODEL:?MODEL is required}"
: "${PROVIDER:?PROVIDER is required}"
: "${API_KEY_FROM:?API_KEY_FROM is required}"
: "${EXPERIMENT_CAP_USD:?EXPERIMENT_CAP_USD is required}"
: "${WORKERS:?WORKERS is required}"

STATUS_FILE="$STATE_DIR/$LABEL.status.json"
RUN_ID_FILE="$STATE_DIR/$LABEL.run_id"
# Clear any ID left by a previous launch: the exit trap must never finalize an earlier run and
# report its results as this one's. The previous run's artifacts are untouched either way.
rm -f "$RUN_ID_FILE"

# Artifacts are the product of the run: never let any path delete them.
export AGENT_BENCH_NEVER_CLEANUP=1
# The controller and the executor are the same machine, so the SSH backend drives localhost.
export AGENT_BENCH_SSH_HOST="${AGENT_BENCH_SSH_HOST:-localhost}"

cd "$ENGINE_DIR"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

write_status() {
  local run_id
  run_id="$(cat "$RUN_ID_FILE" 2>/dev/null || true)"
  if [ -n "$run_id" ]; then
    # A snapshot failure must never mask the run's own outcome, so keep the previous file.
    uv run agent-bench snapshot "$run_id" > "$STATUS_FILE.tmp" 2>/dev/null \
      && mv "$STATUS_FILE.tmp" "$STATUS_FILE" \
      || rm -f "$STATUS_FILE.tmp"
  fi
}

finish() {
  local code=$?
  trap - EXIT INT TERM
  local run_id
  run_id="$(cat "$RUN_ID_FILE" 2>/dev/null || true)"
  if [ -n "$run_id" ]; then
    log "finalizing $run_id (exit $code): grading and reporting whatever completed"
    # Force the tail of the pipeline even if execute failed or was stopped. This is the
    # difference between a stopped run and a lost run.
    uv run agent-bench finalize "$run_id" || log "finalize reported an error; artifacts retained"
    write_status
    log "results: $ENGINE_DIR/runs/$run_id"
  else
    log "no run was created (exit $code); nothing to finalize"
  fi
  log "done (exit $code)"
  exit "$code"
}
trap finish EXIT INT TERM

log "experiment $LABEL: $BENCHMARK / $MODEL via $PROVIDER, ${WORKERS} workers, cap \$$EXPERIMENT_CAP_USD"

if [ -z "${!API_KEY_FROM:-}" ]; then
  log "FATAL: $API_KEY_FROM is not set in the environment"
  exit 78  # EX_CONFIG
fi

PER_TASK_ARGS=()
if [ -n "${PER_TASK_CAP_USD:-}" ]; then
  PER_TASK_ARGS=(--per-task-cost-limit-usd "$PER_TASK_CAP_USD")
fi

# Omitting both means the full benchmark, which is what a real experiment wants. Setting them
# runs a small canary over the identical code path -- the only honest way to validate a VM,
# transport and key before committing the full budget.
EFFORT_ARGS=()
if [ -n "${REASONING_EFFORT:-}" ]; then
  EFFORT_ARGS=(--reasoning-effort "$REASONING_EFFORT")
  log "reasoning effort: $REASONING_EFFORT"
fi

SCOPE_ARGS=()
if [ -n "${SAMPLING:-}" ] && [ -n "${SIZE:-}" ]; then
  SCOPE_ARGS=(--sampling "$SAMPLING" --size "$SIZE")
  log "CANARY scope: --sampling $SAMPLING --size $SIZE"
fi

# Record the resolved spec first. This spends nothing and fails fast on a bad model, provider,
# effort or cap -- far better than discovering it hours into a 500-task run.
log "planning (no spend)"
uv run agent-bench plan \
  --benchmark "$BENCHMARK" \
  --model "$MODEL" \
  --provider "$PROVIDER" \
  --workers "$WORKERS" \
  --budget-usd "$EXPERIMENT_CAP_USD" \
  --label "$LABEL" \
  --api-key-from "$API_KEY_FROM" \
  "${PER_TASK_ARGS[@]}" "${EFFORT_ARGS[@]}" "${SCOPE_ARGS[@]}" \
  > "$STATE_DIR/$LABEL.resolved.yaml" || exit $?
log "resolved spec: $STATE_DIR/$LABEL.resolved.yaml"

RUN_LOG="$STATE_DIR/$LABEL.log"

log "running (log: $RUN_LOG)"
# Run in the background rather than through a pipe: a pipeline's exit status would be the
# reader's, not the run's, and the exit code decides what the trap reports.
# --no-cleanup keeps the workspace; omitting --reasoning-effort means the provider default.
uv run agent-bench run \
  --benchmark "$BENCHMARK" \
  --model "$MODEL" \
  --provider "$PROVIDER" \
  --workers "$WORKERS" \
  --budget-usd "$EXPERIMENT_CAP_USD" \
  --label "$LABEL" \
  --api-key-from "$API_KEY_FROM" \
  --no-cleanup \
  "${PER_TASK_ARGS[@]}" "${EFFORT_ARGS[@]}" "${SCOPE_ARGS[@]}" \
  >> "$RUN_LOG" 2>&1 &
RUN_PID=$!
log "pid $RUN_PID"

capture_run_id() {
  # The CLI prints `created: <run-id>` before any remote work begins. Capture it promptly: the
  # exit trap has nothing to finalize without it, and a short run can finish inside one poll.
  [ -s "$RUN_ID_FILE" ] && return 0
  grep -m1 '^created: ' "$RUN_LOG" 2>/dev/null | sed 's/^created: //' > "$RUN_ID_FILE.tmp" || true
  if [ -s "$RUN_ID_FILE.tmp" ]; then
    mv "$RUN_ID_FILE.tmp" "$RUN_ID_FILE"
    log "run id: $(cat "$RUN_ID_FILE")"
  else
    rm -f "$RUN_ID_FILE.tmp"
  fi
}

# Poll the run ID every 2s but refresh the status file only every 30s: the ID must be captured
# quickly (a canary can finish in seconds), while a snapshot is comparatively expensive.
elapsed=0
while kill -0 "$RUN_PID" 2>/dev/null; do
  capture_run_id
  if [ $((elapsed % 30)) -eq 0 ]; then
    write_status
  fi
  sleep 2
  elapsed=$((elapsed + 2))
done

# Final chance, for a run that finished between polls.
capture_run_id
wait "$RUN_PID"
exit $?
