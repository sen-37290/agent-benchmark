# eval_agent

Reserved for the future user-facing agent system.

This layer may contain multiple agents, coordination, state, and tool bindings. Its benchmark tool
is the public `agent-bench` CLI. It must not reach into `eval_engine` internals or reimplement SSH,
Harbor execution, grading, artifact transfer, or cleanup.

Terminology:

- **eval agent**: the automation system in this directory
- **subject agent**: an agent being evaluated, such as mini-swe-agent
- **eval engine**: the deterministic system in `../eval_engine`

No agent or LLM code is part of v1.

