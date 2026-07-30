#!/bin/bash
set -euo pipefail

# ================================ STANDARD SWE-bench MODE ================================
# This verifier CAPTURES the agent's patch ONLY. It does NOT grade in place.
#
# Grading is done OUT-OF-BAND by the official, hermetic SWE-bench harness
# (scripts/regrade_official.sh): the captured patch is replayed on a FRESH checkout of the
# base commit and scored with the canonical harness. That is the sole source of truth and is
# comparable to the SWE-bench leaderboard.
#
# Why not grade here: Harbor's default in-place ("shared") grading runs the tests inside the
# agent's own, polluted container. The 2026-07-21 smoke measured that it INFLATES the resolve
# rate by +10pp (78% in-place vs 68% hermetic) because side effects the agent left behind
# (pip installs, built C-extensions, untracked files) make tests pass in ways that don't
# transfer to a clean checkout / aren't a mergeable patch. So we skip it entirely.
#
# Harbor requires a reward file to exist, so we write a PLACEHOLDER 0. It is NOT the grade and
# must be ignored (Harbor's job summary will therefore show 0% — that is expected). The real
# resolve rate comes only from the official regrade + aggregate.py.
# ========================================================================================

mkdir -p /logs/verifier
(
  cd /testbed 2>/dev/null || exit 0
  git config --global --add safe.directory /testbed 2>/dev/null || true
  # The image records HEAD before the agent starts. This is intentionally not base_commit:
  # SWE-bench images may add a tracked environment-setup commit above base_commit.
  AGENT_START_COMMIT="$(cat /opt/agent-benchmark-start-commit 2>/dev/null || true)"
  [ -z "$AGENT_START_COMMIT" ] && AGENT_START_COMMIT=HEAD
  git add -A 2>/dev/null || true
  git diff --cached --no-color --binary "$AGENT_START_COMMIT" > /logs/verifier/model_patch.diff 2>/dev/null \
    || git diff --no-color --binary "$AGENT_START_COMMIT" > /logs/verifier/model_patch.diff 2>/dev/null \
    || : > /logs/verifier/model_patch.diff
) || true

# Placeholder reward so Harbor doesn't error (RewardFileNotFoundError). NOT the grade.
echo 0 > /logs/verifier/reward.txt
exit 0
