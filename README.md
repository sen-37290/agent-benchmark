# agent-benchmark

A benchmark evaluation system with two deliberately separate layers:

- `eval_agent/` is the future agentic application that understands user intent and invokes tools.
- `eval_engine/` is the deterministic, agent-free CLI that validates, runs, grades, collects, and
  reports benchmark experiments.

Only `eval_engine/` is implemented in v1. The existing
`../swe-harbor-eval` repository is the behavioral reference for the first benchmark module; it is
not a runtime dependency.

See [`eval_engine/README.md`](eval_engine/README.md) for usage,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for component boundaries, and
[`docs/VM_SMOKE_TEST.md`](docs/VM_SMOKE_TEST.md) for the manual VM integration gate.

The reproducible publication figures live in [`usecase/`](usecase/README.md). Each figure has one
self-contained Python script that downloads its public input data and creates the corresponding PNG.
