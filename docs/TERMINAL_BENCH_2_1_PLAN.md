# Terminal-Bench 2.1 integration plan

Status: approved and implemented; local verification complete, paid VM smoke test pending explicit
approval.

## What Terminal-Bench 2.1 is

Terminal-Bench 2.1 evaluates whether a subject agent can complete realistic, complex tasks in an
isolated terminal environment. The official Harbor package is
`terminal-bench/terminal-bench-2-1`; it contains 89 tasks spanning software engineering, system
administration, scientific computing, security, data science, debugging, and related categories.

Each task provides:

- a natural-language instruction shown to the subject agent;
- a container environment and resource/time limits;
- hidden verifier inputs and `tests/test.sh`;
- an oracle solution used by benchmark maintainers for validation;
- metadata such as category and difficulty.

The default subject agent, Terminus 2, operates against the task environment. The eval
engine does not implement the subject's agent loop.

## Official end-to-end execution

The official native invocation is conceptually:

```bash
harbor run \
  -d terminal-bench/terminal-bench-2-1@sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a \
  -a terminus-2 \
  -m <provider/model> \
  -n <concurrency> \
  -k 1
```

For each selected task, Harbor performs the following sequence:

1. Resolve the pinned dataset from Harbor Hub and download the selected content-addressed tasks.
2. Prepare or pull the task's Docker environment and enforce its CPU, memory, storage, network,
   agent-timeout, and verifier-timeout settings.
3. Install and start the configured subject agent in the environment.
4. Give only the task instruction and working environment to the subject agent.
5. Let the agent inspect files, run commands, and modify the workspace until it finishes or times
   out.
6. After agent execution, copy the hidden verifier files into the environment and run
   `tests/test.sh` against the final workspace.
7. Read the reward written by that verifier, preserve logs/artifacts, and write the trial's
   `result.json`.
8. Aggregate trial rewards and error statistics into the Harbor job result.

Generation and grading are therefore inline parts of one Harbor trial. Running a separate grader
after Harbor would grade the same task twice and is not part of the official Terminal-Bench 2.1
flow.

## How grading works

Grading is task-specific, deterministic code owned by each Terminal-Bench task rather than an LLM
judge or a single central grader.

- Harbor runs the task's hidden `tests/test.sh` after the subject agent stops.
- The script may invoke pytest or task-specific checks against the final container workspace.
- It writes a scalar to `/logs/verifier/reward.txt` (or, in general Harbor tasks,
  `/logs/verifier/reward.json`).
- Harbor parses that file into `verifier_result.rewards` in `result.json`.
- All 89 tasks in the currently pinned Terminal-Bench 2.1 release write binary `0` or `1`.
- A trial succeeds when `reward > 0`. Errors, timeouts, absent rewards, and missing trials count as
  failures.
- With one attempt per task, the official aggregate is accuracy: successful trials divided by all
  selected trials. The official leaderboard requires five attempts per task and additionally
  reports pass@k, but leaderboard submission is outside this first integration.

Important artifact locations inside a Harbor trial are:

```text
<job>/<task>/<trial>/
├── result.json                 # reward, agent/model info, errors, timing, cost/tokens
├── agent/                      # subject-agent logs and trajectory
├── verifier/
│   ├── reward.txt              # authoritative 0/1 reward for Terminal-Bench 2.1
│   └── test-stdout.txt         # verifier output
└── artifacts/                  # task-declared collected artifacts
```

The engine should treat Harbor's verifier reward as authoritative. Unlike the current SWE-bench
integration, Terminal-Bench does not need a second external canonical grader.

## Mapping to the existing eval engine

The common lifecycle remains:

```text
deploy -> prepare -> execute -> grade -> collect -> normalize -> report -> cleanup
```

Its Terminal-Bench interpretation will be:

- `prepare`: validate the pinned catalog, pool, dataset digest, Harbor version, and subject-agent
  version. It must not execute or grade tasks.
- `execute`: invoke one Harbor native job. Harbor downloads the selected tasks, runs the subject
  agent, and immediately runs each task verifier.
- `grade`: do not invoke Harbor again. Validate the already-produced rewards and result artifacts
  and write a benchmark summary.
- `collect`: copy Harbor's raw job directory and logs from the VM with checksum verification.
- `normalize`: produce one common `TaskResult` per selected task with
  `metrics.reward=<Harbor reward>` and `metrics.resolved=(reward > 0)`.
- `report`: report accuracy, mean reward, errors/missing trials, token usage, and measured cost.

This preserves the distinction between benchmark pipeline orchestration and the evaluated
Terminus 2 loop while following Harbor's native inline grading model.

## Confirmed dataset and compatibility facts

- Official name: Terminal-Bench 2.1.
- Harbor package: `terminal-bench/terminal-bench-2-1`.
- Pinned dataset digest:
  `sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a`.
- Full dataset: 89 tasks.
- Difficulty metadata: 4 easy, 55 medium, 30 hard.
- Category metadata: 16 categories; software engineering is the largest with 26 tasks.
- Resource maxima in the pinned source: 4 CPUs, 8192 MB RAM, 10240 MB task storage, no GPUs.
- All tasks permit internet access and have agent/verifier timeouts ranging from minutes to hours.
- Terminal-Bench 2.1 and Harbor use Apache-2.0 licensing.
- The current official repository requires Harbor `>=0.20.0`; the engine's pinned
  `harbor==0.20.0` can be retained.
- The engine's existing mini-swe-agent 2.4.5 is represented in the official Terminal-Bench 2.1
  leaderboard.

## Implementation changes

### Benchmark-owned code

Add `agent_benchmark/benchmarks/terminal_bench/` with:

- `benchmark.py`: prepare validation and post-Harbor grade validation/summary;
- `pool.py`: full, deterministic random, and category-balanced pool creation;
- `results.py`: Terminal-Bench task identity, reward interpretation, and normalization;
- `assets/catalog.json`: the pinned 89-task identifier/digest/category/difficulty catalog with
  source and license provenance;
- `__init__.py`: plugin export.

Add `config/benchmarks/terminal-bench-2.1.yaml` with the package digest, Harbor-native source mode,
runtime dependency extra, grading mode, and trial-error policy. Extend the existing plugin lookup;
do not create a new registry abstraction.

Subject agents are separately owned by `subject_agents/mini_swe_agent.py` and
`subject_agents/terminus_2.py`, with declarative profiles under `config/agents/`. Terminal-Bench
selects Terminus 2 as its default profile, but does not contain agent-specific validation or Harbor
argument construction.

### Pool rules

- No sampling options means all 89 tasks.
- `random` uses seed 42 and returns a deterministic sorted subset.
- `category` shuffles each category with seed 42 and selects round-robin across categories.
- Valid size is 1 through 89.
- The pool records the dataset digest, selected task digests, sampling seed, and category counts.

### Shared Harbor changes

- Keep SWE-bench on local `-p <prepared-dataset>` execution.
- Add a generic Harbor package-dataset mode using `-d <package@digest>` plus repeated
  `--include-task-name` values from the pool.
- Use a deterministic `--job-name <run-id>`, `-k 1`, the requested `-n <workers>`, and the existing
  agent/model/config/provider credential wiring.
- On first execution use `harbor run`; if that job directory already has a Harbor config, use
  `harbor job resume -p <job-dir>` so completed trials are skipped.
- Share only generic Harbor artifact discovery and token/cost/duration extraction. Reward meaning,
  task identity, grading policy, and summary construction remain in the Terminal-Bench package.
- Preserve SWE-bench's fail-fast trial-exception behavior while allowing Terminal-Bench trial
  exceptions to normalize as failed tasks.

### Results and reporting

- Preserve the numeric Harbor reward in `TaskResult.metrics.reward`.
- Set `TaskResult.metrics.resolved` from `reward > 0`.
- No `TaskResult` schema expansion is required because metrics already support floats.
- Add reward to CSV output and accuracy/mean reward to the common report when reward metrics are
  present.
- A malformed reward or a non-error trial without a reward fails the grade stage. A Harbor trial
  exception remains a task-level failure rather than failing the entire pipeline.

### Dependencies and documentation

- Add a `terminalbench` optional dependency extra using Harbor 0.20.0. Terminus 2 is built into
  Harbor; mini-swe-agent 2.4.5 remains installed as a supported alternative subject agent.
- Let remote deployment select the benchmark profile's dependency extra; keep the existing
  SWE-bench extra and grader versions unchanged.
- Document Harbor Hub and Docker-registry network access, Docker/Compose requirements, persistent
  caches, resource requirements, and manual smoke-test gates.
- Do not bundle or redistribute task contents; only the attributed metadata catalog is packaged.

## Resume, cancel, and failure behavior

- An interrupted or partial execute stage leaves the deterministic Harbor job directory intact.
- `agent-bench resume` re-enters execute and delegates partial completion to native
  `harbor job resume`; already completed trials are not rerun.
- A completed trial containing an agent/verifier exception is retained and scored as failure.
- An unexpected trial count causes execute to fail so it can be inspected and resumed.
- Existing VM lease, streaming logs, total-cost watchdog, collection checksums, and cleanup remain
  in force.
- Existing `cancel` semantics remain destructive for the run workspace: it stops owned processes
  and containers, removes the remote run, releases the lease, and makes that run non-resumable.

## Verification plan

- Ruff lint and formatting checks.
- Full unit test suite and explicit SWE-bench regression tests.
- Full/random/category pool determinism and validation tests.
- Fresh/resume and local/package Harbor command construction tests.
- Raw Harbor fixture tests for pass, fail, error, missing, reward, duration, token, and cost fields.
- CLI `plan` semantic tests requiring neither a VM nor a model call.
- Mocked prepare/execute/inline-grade/normalize pipeline test proving grading is not executed twice.
- Wheel-content test for the benchmark YAML and metadata catalog.
- No real VM or paid model run without separate approval.

## Proposed manual smoke test

```bash
cd eval_engine
uv sync --extra terminalbench

uv run agent-bench plan \
  --benchmark terminal-bench-2.1 \
  --model glm-5.2 \
  --reasoning-effort xhigh \
  --provider friendli \
  --byok \
  --workers 1 \
  --budget-usd 5 \
  --sampling random \
  --size 1
```

After separate approval, change `plan` to `run` for the paid one-task VM smoke test.

An official mini-swe-agent 2.4.5 leaderboard submission reports 445 trials, $198.05 total cost,
and 687 seconds average duration. A rough one-attempt extrapolation is about $39.61 and 17 hours
of aggregate trial time for all 89 tasks; four workers have an idealized lower bound near 4.3
hours. Model pricing, first-time image pulls, VM capacity, and resource contention can materially
change these figures.

## Explicitly deferred

- Official leaderboard upload, five attempts per task, and pass@k.
- Eval automation agent or any engine-owned subject-agent loop.
- Multiple-VM scheduling or concurrent engine runs.
- Web UI and Harbor UI integration.
- LegalBench and unrelated engine redesign.

## Primary references

- Terminal-Bench 2.1 release: <https://www.tbench.ai/news/terminal-bench-2-1>
- Official run instructions: <https://www.tbench.ai/docs/run-terminal-bench-2-1>
- Official dataset source: <https://github.com/harbor-framework/terminal-bench-2-1>
- Harbor Hub dataset: <https://hub.harborframework.com/datasets/terminal-bench/terminal-bench-2-1/latest>
- Harbor datasets documentation: <https://www.harborframework.com/docs/datasets>
- Harbor result/artifact documentation:
  <https://www.harborframework.com/docs/run-jobs/results-and-artifacts>
