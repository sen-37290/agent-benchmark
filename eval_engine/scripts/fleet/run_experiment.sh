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

# Concurrent services on one VM receive a label-specific LAUNCH_ENV from systemd. Retain the
# shared path as a backwards-compatible fallback for older units.
LAUNCH_ENV="${LAUNCH_ENV:-$STATE_DIR/launch.env}"
if [ -f "$LAUNCH_ENV" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$LAUNCH_ENV"
  set +a
  rm -f "$LAUNCH_ENV"
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

# Dependency-specific campaigns must never fall back to whatever version happens to be in the
# VM cache. `uv run` performs its normal frozen-project sync first; inspect that exact environment
# and stop before creating a run if the repaired dependency is not active.
if [ -n "${REQUIRED_LITELLM_VERSION:-}" ]; then
  INSTALLED_LITELLM_VERSION="$(
    uv run python -c 'import importlib.metadata; print(importlib.metadata.version("litellm"))'
  )" || exit $?
  if [ "$INSTALLED_LITELLM_VERSION" != "$REQUIRED_LITELLM_VERSION" ]; then
    log "FATAL: LiteLLM $INSTALLED_LITELLM_VERSION installed; $REQUIRED_LITELLM_VERSION required"
    exit 78
  fi
  log "verified LiteLLM $INSTALLED_LITELLM_VERSION"
fi

PER_TASK_ARGS=()
if [ -n "${PER_TASK_CAP_USD:-}" ]; then
  PER_TASK_ARGS=(--per-task-cost-limit-usd "$PER_TASK_CAP_USD")
fi
# SWE-bench Verified locks the per-task cap to the official $3 so nobody drifts off-protocol by
# accident. Departing from it has to be stated in the experiment row, never inferred from the cap
# alone -- that is the difference between a deliberate re-run and a silent protocol change.
if [ "${ALLOW_COST_LIMIT_OVERRIDE:-0}" = "1" ]; then
  PER_TASK_ARGS+=(--allow-cost-limit-override)
  log "per-task cap \$${PER_TASK_CAP_USD:-?} overrides the benchmark's locked official value"
fi

# Two ways to give slow tasks more room, and they are not equivalent. --no-timeout removes the
# deadline outright, which leaves the per-task dollar cap as the ONLY bound on a task (Terminus 2
# allows 1,000,000 episodes); on a cheap model that can mean days per task. A finite multiplier
# scales the official 900/1800/3600s limits instead and still guarantees the task ends.
TIMEOUT_ARGS=()
if [ "${NO_TIMEOUT:-0}" = "1" ]; then
  TIMEOUT_ARGS=(--no-timeout)
  log "Harbor agent-execution deadline DISABLED (--no-timeout); only the per-task cap bounds a task"
elif [ -n "${AGENT_TIMEOUT_MULTIPLIER:-}" ]; then
  TIMEOUT_ARGS=(--agent-timeout-multiplier "$AGENT_TIMEOUT_MULTIPLIER")
  log "Harbor agent deadline scaled x${AGENT_TIMEOUT_MULTIPLIER}"
fi

# A subset re-run pins exact task ids via the benchmark's pin env var, so only the named tasks run
# (e.g. redo the ones a prior run timed out on) instead of the whole benchmark.
if [ -n "${PIN_INSTANCES:-}" ] && [ -f "${PIN_INSTANCES}" ]; then
  case "$BENCHMARK" in
    terminal-bench*) export TERMINAL_BENCH_PIN_INSTANCES="$PIN_INSTANCES" ;;
    cybergym*)       export CYBERGYM_PIN_INSTANCES="$PIN_INSTANCES" ;;
    swebench*)       export SWEBENCH_PIN_INSTANCES="$PIN_INSTANCES" ;;
  esac
  log "pinned subset: $(python3 -c "import json;print(len(json.load(open('$PIN_INSTANCES'))['instance_ids']))" 2>/dev/null) tasks from $PIN_INSTANCES"
fi

# This runs outside the benchmark process, before its first model request. It creates the exact
# full/pinned SWE pool, pulls each generation/grading image with bounded concurrency, verifies
# every tag through Docker, and exits non-zero after three retries if anything is unavailable.
if [ "${STRICT_SWE_IMAGE_GATE:-0}" = "1" ]; then
  IMAGE_POOL="$STATE_DIR/$LABEL.image-pool.json"
  IMAGE_LOG="$STATE_DIR/$LABEL.image-prepull.log"
  log "strict SWE image gate: all images must be local before any LLM request"
  uv run python scripts/fleet/strict_swe_preflight.py \
    --pool "$IMAGE_POOL" \
    --log "$IMAGE_LOG" \
    --workers "$WORKERS" \
    --retries 3 || exit $?
  log "strict SWE image gate passed"
fi

# Probe the same LiteLLM Responses mapper that silently dropped `max` in 1.94.0. This is the first
# LLM request made by the controller, and it only runs after the complete image gate above.
if [ "${VERIFY_RESPONSES_EFFORT:-0}" = "1" ]; then
  EFFORT_LOG="$STATE_DIR/$LABEL.reasoning-effort-preflight.json"
  log "verifying provider-echoed reasoning effort: ${REASONING_EFFORT:-<unset>}"
  uv run python scripts/fleet/verify_responses_effort.py \
    --model "$MODEL" \
    --api-key-env "$API_KEY_FROM" \
    --expected "$REASONING_EFFORT" \
    --required-litellm-version "$REQUIRED_LITELLM_VERSION" \
    --log "$EFFORT_LOG" \
    --retries 3 || exit $?
  log "reasoning-effort preflight passed"
fi

BUDGET_ARGS=(--budget-usd "$EXPERIMENT_CAP_USD")
if [ "${NO_BUDGET_LIMIT:-0}" = "1" ]; then
  # Experiment-level cost cap disabled by request; the per-task cap still applies.
  BUDGET_ARGS=(--no-budget-limit)
  log "experiment cost cap DISABLED (per-task cap still enforced)"
fi

EFFORT_ARGS=()
if [ -n "${REASONING_EFFORT:-}" ]; then
  EFFORT_ARGS=(--reasoning-effort "$REASONING_EFFORT")
  log "reasoning effort: $REASONING_EFFORT"
fi

TARGET_ARGS=()
if [ -n "${BACKUP_TARGET:-}" ]; then
  TARGET_ARGS=(--backup "$BACKUP_TARGET")
  log "isolated execution target: $BACKUP_TARGET"
fi

# Anthropic server-side fallback. A refused request is retried on another model inside the same
# API call, so a task the primary model declines is still attempted instead of being lost to the
# empty-response parse loop that cost this benchmark 13 tasks. Only a safety-classifier refusal
# triggers it; rate limits and server errors are returned unchanged.
FALLBACK_ARGS=()
if [ -n "${ANTHROPIC_FALLBACKS:-}" ]; then
  FALLBACK_ARGS=(--anthropic-fallbacks "$ANTHROPIC_FALLBACKS")
  log "anthropic server-side fallback: $ANTHROPIC_FALLBACKS"
fi
if [ -n "${OPENAI_FALLBACKS:-}" ]; then
  FALLBACK_ARGS+=(--openai-fallbacks "$OPENAI_FALLBACKS")
  log "openai client-side model fallback: $OPENAI_FALLBACKS"
fi

# Omitting both means the full benchmark, which is what a real experiment wants. Setting them
# runs a small canary over the identical code path -- the only honest way to validate a VM,
# transport and key before committing the full budget.
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
  "${BUDGET_ARGS[@]}" \
  --label "$LABEL" \
  --api-key-from "$API_KEY_FROM" \
  "${PER_TASK_ARGS[@]}" "${EFFORT_ARGS[@]}" "${TIMEOUT_ARGS[@]}" "${SCOPE_ARGS[@]}" \
  "${FALLBACK_ARGS[@]}" "${TARGET_ARGS[@]}" \
  > "$STATE_DIR/$LABEL.resolved.yaml" || exit $?
log "resolved spec: $STATE_DIR/$LABEL.resolved.yaml"

RUN_LOG="$STATE_DIR/$LABEL.log"
# Truncate: this file is appended to, and a redeploy of the same label on the same VM would
# otherwise leave the PREVIOUS run's `created:` line in it -- which capture_run_id would then read
# as this run's id, pointing the exit-trap finalize at the wrong (earlier) run.
: > "$RUN_LOG"

log "running (log: $RUN_LOG)"
# Run in the background rather than through a pipe: a pipeline's exit status would be the
# reader's, not the run's, and the exit code decides what the trap reports.
# --no-cleanup keeps the workspace; omitting --reasoning-effort means the provider default.
uv run agent-bench run \
  --benchmark "$BENCHMARK" \
  --model "$MODEL" \
  --provider "$PROVIDER" \
  --workers "$WORKERS" \
  "${BUDGET_ARGS[@]}" \
  --label "$LABEL" \
  --api-key-from "$API_KEY_FROM" \
  --no-cleanup \
  "${PER_TASK_ARGS[@]}" "${EFFORT_ARGS[@]}" "${TIMEOUT_ARGS[@]}" "${SCOPE_ARGS[@]}" \
  "${FALLBACK_ARGS[@]}" "${TARGET_ARGS[@]}" \
  >> "$RUN_LOG" 2>&1 &
RUN_PID=$!
log "pid $RUN_PID"

capture_run_id() {
  # The CLI prints `created: <run-id>` before any remote work begins. Capture it promptly: the
  # exit trap has nothing to finalize without it, and a short run can finish inside one poll.
  [ -s "$RUN_ID_FILE" ] && return 0
  # tail -1: the current run's `created:` line is the last one written, so even a stale log
  # (e.g. a redeploy that did not truncate) cannot make us finalize an earlier run.
  grep '^created: ' "$RUN_LOG" 2>/dev/null | tail -1 | sed 's/^created: //' > "$RUN_ID_FILE.tmp" || true
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
