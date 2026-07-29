# eval_engine

The deterministic execution layer of `agent-benchmark`. It does not contain an LLM or an agent
loop. Humans—and later `eval_agent`—invoke the same CLI.

## Commands

```bash
uv sync --extra swebench

# Validate and show the fully resolved request without creating a run.
uv run agent-bench plan \
  --benchmark swebench-verified \
  --model glm-5.2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --workers 2 \
  --budget-usd 10 \
  --sampling random \
  --size 2

# Execute the complete remote pipeline.
uv run agent-bench run <same arguments>

uv run agent-bench status <run-id>
uv run agent-bench resume <run-id>
uv run agent-bench report <run-id>
uv run agent-bench profiles list
uv run agent-bench remote doctor --target fixed-vm
```

Set `AGENT_BENCH_SSH_HOST` to an SSH host or alias. API keys are read from the environment named by
the selected model profile and are never written into request or resolved specs.

Sampling is optional. Omitting both `--sampling` and `--size` creates a run-specific pool containing
the full benchmark (500 tasks for SWE-bench Verified). SWE-bench accepts `random` and `domain`.
Generated files live under `pools/` locally, are ignored by Git, and are copied into the permanent
run bundle as `inputs/pool.json`.

Provider arguments are user-facing routes:

| `--provider` | Internal transport | Routing |
|---|---|---|
| `openrouter` | OpenRouter | OpenRouter default routing |
| `friendli` | OpenRouter | `provider.only=[friendli]` |
| `anthropic` | Anthropic via litellm | Anthropic first-party API |

Add `--byok` to the Friendli form to disable OpenRouter fallback. The selected provider, internal
API transport, and route are all recorded separately in `resolved.yaml`.

## Run lifecycle

Every run is stored locally under `runs/<run-id>/`. The original CLI request, immutable resolved
spec, event journal, raw artifacts, normalized task results, and report stay together. The remote
workspace is deleted only after artifact checksums have been verified locally. Shared Docker,
dataset, and dependency caches are retained.
