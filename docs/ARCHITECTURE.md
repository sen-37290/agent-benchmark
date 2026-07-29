# Architecture

## System boundary

`agent-benchmark` has two independent layers:

```text
eval_agent  --calls CLI as a tool-->  eval_engine  --SSH-->  fixed VM
                                            |
                                            +--> benchmark plugin
                                            +--> harness adapter
                                            +--> canonical grader
```

`eval_agent` is a future agentic application. It may interpret requests, ask clarifying questions,
coordinate multiple agents, call `agent-bench`, monitor runs, and explain reports. It does not own
benchmark execution.

`eval_engine` is deterministic and works without an agent or LLM. It owns validation, immutable
spec resolution, remote execution, grading, artifact integrity, normalization, reporting, and
remote cleanup.

The subject agent being evaluated—mini-swe-agent in v1—is an opaque harness setting. Its loop is
not implemented by the engine.

## Extension boundaries

- `BenchmarkPlugin` owns dataset preparation, canonical grading, and conversion to `TaskResult`.
- Each benchmark plugin creates its own run-specific pool. With no sampling arguments it must use
  the full benchmark; optional sampling strategies are benchmark-specific.
- `HarnessAdapter` executes a configured subject against prepared tasks.
- `SSHBackend` deploys a versioned engine, runs remote stages, fetches artifacts, verifies checksums,
  and removes only the completed run workspace.
- `Reporter` consumes normalized `TaskResult` records and never reads Harbor-specific files.

The lifecycle is fixed while implementations vary:

```text
validate -> deploy -> prepare -> execute -> grade -> collect -> normalize -> report -> cleanup
```

Terminal-Bench can later reuse the Harbor harness with a different benchmark plugin. A non-agentic
benchmark such as LegalBench can provide its own harness while keeping the same run lifecycle and
result schema.

## Run invariants

- The local `runs/<run-id>/` bundle is the permanent source of truth.
- `request.yaml` records public CLI arguments; `resolved.yaml` records every expanded setting.
- Generated pools are ignored by Git and copied to `inputs/pool.json` inside the run bundle.
- Neither file contains secret values.
- Stage transitions are journaled atomically and failed stages are resumable.
- Official SWE-bench grading is authoritative; Harbor's capture-only reward is ignored.
- Remote run data is deleted only after local checksum verification and report creation.
- Shared Docker, dependency, source, and dataset caches survive successful runs.
- A VM-wide lease permits one active run by default.

## SWE-bench version boundary

The two SWE-bench installations serve different purposes and are intentionally isolated:

- Harbor adapter `v0.20.0` uses `swebench==4.1.0` only to materialize Harbor task directories.
- The canonical grader uses `swebench==4.0.3`, matching the existing evaluation pipeline.

Both versions are stored in the resolved benchmark settings.
