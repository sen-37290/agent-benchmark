# Aider Polyglot Benchmark integration plan

Status: **implemented and locally verified; real-VM smoke test requires explicit approval**
Prepared: 2026-07-29
Scope: add Aider Polyglot to `eval_engine`; no VM or paid-model execution was performed.

## 1. Fixed terminology

The integration will use these names consistently.

| Role | Official/product name | `agent-bench` name |
|---|---|---|
| Benchmark | Aider Polyglot Benchmark | benchmark profile `aider-polyglot` |
| Harness | Aider Benchmark Harness | harness `aider-native` |
| Agent loop | Aider Coder | agent profile `aider` |
| Dataset | Aider Polyglot exercises | `Aider-AI/polyglot-benchmark` |

“Aider Polyglot” must not be used as if all four roles were one component. In particular, a
leaderboard-comparable score means the pinned exercises were run by the pinned Aider Benchmark
Harness using the Aider Coder loop. Running the same exercises with a different agent is a distinct
agent-benchmark evaluation, not an official Aider leaderboard result.

## 2. Executive decision

Add three explicitly separate components:

1. `benchmarks/aider_polyglot`: owns the pinned exercise catalog, pool generation, dataset
   verification/materialization, validation of native result artifacts, normalization, and
   benchmark-specific aggregate metrics.
2. `harnesses/aider_native.py`: owns invocation and resumption of the official Aider Benchmark
   Harness inside its Docker environment. It does not parse or grade benchmark results.
3. `agents/aider.py`: owns Aider Coder model/provider/edit-format invocation settings. It does not
   own exercise discovery, testing, aggregation, or the full `benchmark.py` lifecycle.

Do **not** convert the official path to Harbor. The official runner creates an Aider `Coder`, edits
the solution files, runs language tests after each turn, feeds failure output into the same coder,
and records Aider-specific formatting telemetry. Replacing that loop with Harbor would change the
evaluated system and remove direct leaderboard comparability.

The first supported combination is therefore:

| Benchmark | Agent | Harness | Engine support | Meaning | E2E status initially |
|---|---|---|---|---|---|
| `aider-polyglot` | `aider` (default) | `aider-native` | Supported | Leaderboard-comparable when full/pinned/default-two-try conditions hold | Mocked only |
| `aider-polyglot` | `mini-swe-agent` | — | Not in initial implementation | Technically possible only through a separate task adapter/runner; non-official | Not tested |
| `aider-polyglot` | `terminus-2` | — | Not in initial implementation | Technically possible only through a separate task adapter/runner; non-official | Not tested |
| SWE-bench / Terminal-Bench | `aider` | Harbor | Not in initial implementation | A Harbor-compatible Aider subject adapter would be needed | Not tested |

This is compatibility validation, not coupling model profiles to agents. `--agent` remains the
selection mechanism, benchmark profiles still only declare `default_agent`, and model profiles
remain agent-free. An unsupported benchmark × agent pairing must fail during `plan` with a clear
compatibility error; it must not silently select another harness or score type.

## 3. Confirmed official facts

### Identity and revisions

- Official name used here: **Aider Polyglot Benchmark**. The official site calls its table the
  “Aider polyglot coding leaderboard.”
- Dataset: `https://github.com/Aider-AI/polyglot-benchmark`.
- Dataset has no release/tag. Its current shallow-cloned `main` is the repository's single commit,
  `7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f` (2024-12-22). Pin this full SHA.
- Aider has stable release `v0.86.0`, commit
  `a4be6ccd87ebaa59b361f3f028d116ce1761b626` (2025-08-09). Its complete `benchmark/` directory is
  identical to the inspected 2026-05-22 main checkout (`5dc9490bb35f...`), so pin the stable tag's
  full commit rather than a development main SHA.
- The pinned official `benchmark/benchmark.py` SHA-256 is
  `ba350b7b3ebcc9da6f588c0724dade552791b3c5edd9f16641eadb5931de9c30`.
- The pinned official `benchmark/Dockerfile` SHA-256 is
  `352813bb478c35b88981d03a23679b33f5d1a78871c53173a68b52422fe68e80`.
- The current stable Aider package version is `0.86.0`; current source identifies itself as
  `0.86.3.dev`. Production must use stable `0.86.0` plus its source commit.
- Leaderboard records are not all produced by one Aider version. Examples range across development
  versions and commits (for example 0.69.2.dev, 0.85.1.dev, and 0.86.2.dev). A result can be
  method-compatible with the leaderboard, but exact reproduction of a particular row also requires
  that row's Aider commit/version, model snapshot, edit format, reasoning settings, and provider.

Sources: [leaderboard](https://aider.chat/docs/leaderboards/),
[Aider benchmark README](https://github.com/Aider-AI/aider/blob/a4be6ccd87ebaa59b361f3f028d116ce1761b626/benchmark/README.md),
[runner source](https://github.com/Aider-AI/aider/blob/a4be6ccd87ebaa59b361f3f028d116ce1761b626/benchmark/benchmark.py),
[dataset](https://github.com/Aider-AI/polyglot-benchmark/tree/7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f).

### Dataset composition and provenance

The full benchmark has 225 exercises:

| Language | Count | Test command selected by the official runner |
|---|---:|---|
| C++ | 26 | `/aider/benchmark/cpp-test.sh` |
| Go | 39 | `go test ./...` |
| Java | 47 | `./gradlew test` |
| JavaScript | 49 | `/aider/benchmark/npm-test.sh` |
| Python | 34 | `pytest` |
| Rust | 30 | `cargo test -- --include-ignored` |
| Total | 225 | — |

The source set started from 697 Exercism exercises in these languages. Seven models attempted the
full set; the published 225 are the harder exercises solved by three or fewer of those models. It
is therefore a curated snapshot, not a live mirror of current Exercism tracks.

Canonical engine task IDs will be `<language>/<exercise-slug>`, for example
`cpp/all-your-base`. The official on-disk identity is
`<language>/exercises/practice/<exercise-slug>`; using the shortened stable ID avoids ambiguous
slugs that occur in multiple languages while retaining a reversible mapping.

Each task contains `.meta/config.json`, `.docs` instructions, solution stubs, example/support files,
and tests. The runner derives editable solution files and hidden test/example files from the
config. It excludes `.meta`, `.docs`, test/example files, `CMakeLists.txt`, and `Cargo.toml` from
files offered for editing.

The dataset repository has no root license file and its README delegates licensing to the six
upstream Exercism track repositories. Some copied exercise directories include per-exercise MIT
licenses (all 49 inspected JavaScript exercises); a blanket repository-wide license must not be
inferred. Consequently, do not redistribute exercises or tests in the engine wheel. Clone the
pinned repository at prepare time, retain its README/per-file notices, and include only a factual
catalog (IDs, language, path, digests) plus attribution/source links in our package. Legal review is
required before mirroring or publishing a dataset archive or Docker image that embeds the dataset.

Source: [dataset README](https://github.com/Aider-AI/polyglot-benchmark/blob/7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f/README.md),
[benchmark design write-up](https://aider.chat/2024/12/21/polyglot.html).

### Official setup and command

Official setup is:

```bash
git clone https://github.com/Aider-AI/aider.git
cd aider
mkdir tmp.benchmarks
git clone https://github.com/Aider-AI/polyglot-benchmark tmp.benchmarks/polyglot-benchmark
./benchmark/docker_build.sh
./benchmark/docker.sh
# inside the container
pip install -e .[dev]
./benchmark/benchmark.py RUN_NAME \
  --model MODEL \
  --edit-format EDIT_FORMAT \
  --threads WORKERS \
  --exercises-dir polyglot-benchmark
```

Relevant current CLI options verified in source are:

- `--model/-m`, `--edit-format/-e`, `--editor-model`, `--editor-edit-format`
- `--threads/-t` (default 1), `--tries/-r` (default 2), `--num-tests/-n` (default all)
- `--languages/-l` and substring `--keywords/-k`
- `--exercises-dir` (default `polyglot-benchmark`)
- `--reasoning-effort`, `--thinking-tokens`, `--num-ctx`
- `--read-model-settings` for custom model/edit/provider parameters
- `--cont`, `--new`, `--clean`, `--replay`, `--stats`, `--stats-languages`
- diagnostic `--no-aider`, `--no-unit-tests`, and `--verbose`

There is no exact task-ID list option, no random seed option, no per-task model-call timeout option,
and no built-in dollar budget option. `--num-tests` is applied after an unseeded
`random.shuffle`, so its subset is not reproducible. `--keywords` is substring matching and can
duplicate entries when multiple keywords match. The engine must not use either feature for its
pool semantics. It will instead materialize a run-specific exercises directory containing exactly
the selected task paths; the native runner then sees that directory as its complete dataset.

### Task and attempt lifecycle

For each exercise the native runner:

1. Copies the selected dataset's practice tree into the run directory once.
2. Reads `.meta/config.json`, restores every editable solution file from the pinned original, and
   keeps test/example/config/docs files out of the Aider file set.
3. Builds the prompt from optional introduction, instructions, optional appended instructions, and
   the official addendum naming editable files.
4. Creates one Aider `Model`, one `InputOutput`, and one Aider `Coder` for the task with `use_git=False`,
   no streaming, prompt caching enabled, and no shell-command suggestions.
5. Calls the same coder once per try. After a failed test, it sends normalized test output plus the
   official “tests are correct; fix the code” prompt. The edited source, coder conversation, and
   task state are retained between attempts. A passing task stops early.
6. Copies hidden test files from the untouched source immediately before each test run. Java
   `@Disabled(...)` annotations are removed before execution.
7. Runs the language test command with a fixed 180-second test timeout. Timeout becomes a failed
   attempt and increments `test_timeouts`; it does not abort the task.
8. Removes Rust `target/debug`, Java `build`, and JavaScript `node_modules` after the task and writes
   `.aider.results.json`.

The harness sets Aider API retry timeouts to 24 hours, which is retry/backoff policy rather than a
task deadline. There is no outer task timeout. Engine cancellation must terminate the native
Docker container/process group; an engine-side configurable outer timeout should be deferred unless
required by smoke-test evidence, because inventing one changes official behavior.

Each task has its own copied directory. Parallel threads mutate disjoint task directories. Shared
language/package caches inside one container may be accessed concurrently, while task-local Rust,
Java, and npm build outputs remain isolated. The official implementation is thread-based and does
not claim process-level cache reproducibility.

### Grading, artifacts, and metrics

Generation and grading are intentionally coupled in the official command: every Aider turn is
immediately followed by its test, and failed test output is the next turn's input. Splitting tests
into a later engine `grade` stage would change the two-attempt semantics. Therefore `execute` must
run both generation and inline tests; `grade` only validates artifacts and recomputes aggregates.

Per-task raw artifact:

```text
<run-output>/<language>/exercises/practice/<slug>/
├── .aider.results.json
├── .aider.chat.history.md
├── edited solution files
├── .docs/ and .meta/
└── restored hidden tests (after first test execution)
```

`.aider.results.json` contains `testcase`, `model`, `edit_format`, `tests_outcomes`, `cost`,
`duration`, `test_timeouts`, Aider `commit_hash`, malformed/error/user-ask/context counters,
syntax/indentation/lazy-comment counters, reasoning/thinking settings, prompt/completion token
totals, and request/response hashes. An uncaught task exception is written as
`{"exception": <traceback>}`. A truncated/invalid result JSON is ignored by official stats and is
eligible to be redone on continuation.

Semantics:

- An attempt passes only when the language test command exits zero. There is no partial credit.
- `tests_outcomes[i]` is the observed pass/fail for attempt `i+1`; successful tasks stop early.
- `pass_rate_1`: tasks that passed first try divided by completed result files.
- `pass_rate_2`: tasks that passed by the end of try 2 divided by completed result files. A task
  that passes try 1 is carried forward as passed for later pass-rate columns.
- The leaderboard's **Percent correct** is `pass_rate_2`.
- Official stats warn about incomplete runs but use `completed_tests` as the pass-rate denominator,
  not all discovered tasks. For engine reports, preserve that exact official value and also report
  a conservative pool-denominator value so missing/error tasks cannot disappear.
- Malformed edit responses are counted by Aider. They do not independently determine score: the
  resulting working tree is tested, and only test exit status passes the attempt.
- Syntax/indentation errors and nonzero test output are failed attempts, not separate reward types.
- Test timeout is a failed attempt. An uncaught task exception has no passing outcome.
- “Correct edit format” is `percent_cases_well_formed = 1 - tasks_with_any_malformed_response /
  completed_tasks`; it is format compliance telemetry, not test correctness.
- `cost` and prompt/completion tokens are cumulative per task from Aider Coder. Cached-token counts
  are not in the native task result. Missing cost/tokens must normalize to `null`, never zero; an
  explicit numeric zero from the raw artifact remains zero.

Official stats are printed YAML-like text rather than saved automatically. The engine will preserve
native per-task files, capture `--stats` stdout as `artifacts/aider-native/official-summary.txt`, and
also write a validated JSON aggregate for stable machine consumption.

### Environment

The official Dockerfile uses Ubuntu Jammy (`buildpack-deps:jammy`), Python 3.11, OpenJDK 21,
Go 1.21.5 (amd64 and arm64 branches), Node 20.x plus pinned JS packages, Rust installed from the
floating rustup script, CMake/Boost/TBB, and development installation of the checked-out Aider.
The official wrapper caps memory and swap at 12 GiB. The base image, apt packages, Node 20.x setup,
and Rust channel are not digest/version pinned by upstream, so the official Dockerfile is not fully
bit-reproducible even when its source commit is pinned.

Implementation must record the verified Dockerfile hash, resulting local image ID/RepoDigest,
architecture, and observed toolchain versions in run metadata. It must not claim a stable image
digest until an approved immutable image has actually been built and published. Altering the
official Dockerfile to pin newer toolchains would create a different environment; that should be a
separate future profile/version.

The Dockerfile handles x86_64 and aarch64 for Go, so Apple Silicon can build it, but leaderboard
runs are not documented as architecture-normalized. Production execution remains the existing
Linux Docker VM. Required capacity is at least the official 12 GiB container allowance, plus CPU
for selected workers and disk for two source clones, the image, six toolchains, dependency caches,
and copied run trees. Recommend >=4 vCPU, >=16 GiB RAM, and >=30 GiB free disk for a first full run;
this sizing is an engineering inference, not an upstream guarantee.

Network is required during initial clone/image build, package/toolchain image build, and model API
calls. Java Gradle, Rust Cargo, and JavaScript npm dependencies can require network/cache warming
during tests. Dependencies are not universally installed once per task: the Docker image preloads
global JS dependencies, while task commands may populate task-local or shared caches. A no-network
post-warm run is not guaranteed by upstream.

The exercises and tests are public and derived from Exercism, so training-data contamination is a
material benchmark limitation. Scores measure the Aider+model system on this public snapshot and
must not be described as uncontaminated generalization.

## 4. Existing engine contract and exact mapping

### Existing contract confirmed

- `BenchmarkPlugin`: pool, prepare, canonical grade/validation, normalization.
- `AgentAdapter`: selected-agent invocation/model/provider conversion.
- `HarnessAdapter`: execution orchestration against prepared tasks.
- Benchmark YAML owns `default_agent`; model YAML has no agent selection.
- CLI `--agent` overrides `default_agent`.
- Pipeline: deploy → prepare → execute → grade → collect → normalize → report → cleanup.
- Remote VM lease allows one run; stage state supports retry/resume; cancel kills run-owned
  processes/containers, deletes the remote run, and makes the local run non-resumable.
- Harbor resume uses an existing Harbor job; common budget polling currently understands only
  Harbor `result.json`, so it cannot measure Aider native artifacts without an extension.
- `TaskResult.metrics` already accepts scalar booleans/numbers/null and can represent two-attempt
  outcomes without a schema-version change.

### Lifecycle mapping

| Engine stage | Aider Polyglot behavior |
|---|---|
| deploy | Existing SSH source/spec/pool transfer and `aider` dependency-extra selection. Secret file contains only the resolved model profile's required key. |
| prepare | Clone/fetch Aider and dataset into shared cache; verify exact SHAs and official file hashes; build/inspect `agent-bench/aider-native:<runner-sha>`; verify observed toolchains; materialize only selected exercises into a run-owned input tree; write provenance manifest and generated Aider model-settings YAML. |
| execute | `aider-native` starts the pinned image with the run tree mounted, invokes pinned `benchmark.py` with exact run output, model, edit format, reasoning effort, two tries, workers, and selected exercises directory. It streams redacted logs and resumes by reusing the same output directory. Tests happen inline exactly once per attempt. |
| grade | Do not rerun tests. Validate one parseable result or explicit task exception per pool ID, reject duplicates/unknown IDs, verify model/edit-format/commit/tries provenance, generate native stats output and stable official JSON summary. Missing results make the stage incomplete/failing, not silently smaller. |
| collect | Existing manifest, rsync, checksum verification; collect native result tree, provenance, diff/source snapshots, summary, and logs. |
| normalize | One `TaskResult` per pool task. `resolved` means passed by attempt 2; preserve attempt booleans and Aider telemetry in metrics. |
| report | Common report plus Aider-specific official completed-denominator `pass_rate_1/2`, conservative pool-denominator rates, well-formed rate, timeout/error/malformed counts, cost/tokens, language breakdown, and comparability label. |
| cleanup | Existing remote cleanup and lease release; cached clones/image/toolchain caches remain. |

`execute` and `grade` must never both run tests. The official runner's inline test feedback is part
of the Aider Coder agent loop and belongs to native execution. Grade is artifact validation only,
as Terminal-Bench already does for Harbor inline verification.

### Normalized task result

No `TaskResult` schema change is required initially. Use:

```json
{
  "status": "completed",
  "metrics": {
    "resolved": true,
    "pass_at_1": false,
    "pass_at_2": true,
    "attempts_executed": 2,
    "malformed_responses": 0,
    "test_timeouts": 0,
    "syntax_errors": 0,
    "indentation_errors": 0,
    "edit_format_well_formed": true
  },
  "cost_usd": 0.12,
  "input_tokens": 1234,
  "output_tokens": 567,
  "cached_tokens": null
}
```

For a first-try pass, both `pass_at_1` and cumulative `pass_at_2` are true and
`attempts_executed` is 1. For a two-try failure both are false. A valid raw result with an uncaught
exception maps to `status=error`; absent or invalid final artifact maps to `missing` during
normalization, although grade should normally block completion first. Preserve
`.aider.results.json`, chat history, final source files, and a generated diff in `raw_artifacts`.

## 5. Pool and sampling design

Package a generated immutable `assets/catalog.json` containing dataset SHA, 225 stable IDs,
language, source-relative path, and task tree digest. Do not package tests or prompts.

- No `--sampling` and no `--size`: all 225 tasks.
- `--sampling random --size N`: deterministic `random.Random(42)` sample, sorted by stable ID.
- `--sampling language --size N`: deterministic round-robin across sorted language buckets, using
  independently shuffled queues seeded with 42, then sorted by ID. This matches existing benchmark-
  owned balanced sampling patterns without pretending a partial sample is leaderboard-comparable.
- Reject other strategies and N outside 1..225.
- Pool JSON records seed, dataset SHA, selected IDs, per-language counts, and task digests.

Only a full 225-task run on the pinned dataset with `aider`, `aider-native`, two tries, no replay,
and an explicitly recorded edit format may receive `official_comparability: method-compatible`.
Samples are smoke/general evaluations and receive `official_comparability: false`.

## 6. Agent, model, provider, and edit-format handling

Add agent profile `aider` version `0.86.0`. Its adapter returns native invocation data rather than
Harbor `--ak` arguments: model name, edit format, reasoning effort, optional model-settings file,
and secret environment. Do not put Aider selection in any model YAML.

Resolved model names:

- OpenRouter: prefix the engine model ID with `openrouter/` unless already present.
- Anthropic: use the LiteLLM/Aider provider-qualified Anthropic model name and
  `ANTHROPIC_API_KEY`.
- Reasoning: pass the engine value through official `--reasoning-effort`.
- Edit format: add benchmark setting `edit_format: auto`; `auto` omits `--edit-format` and uses the
  pinned Aider model setting. For reliable cross-run comparison, the resolved spec must record the
  effective format observed in every task result. A future CLI override should be an explicit
  benchmark setting, not a model profile selecting an agent.
- Custom provider routing: generated `--read-model-settings` can add Aider `extra_params`. OpenRouter
  Friendli-only routing would require `extra_body.provider.only=[friendli]` and
  `allow_fallbacks=false`. This exact request body must be covered by a mocked LiteLLM call test
  before declaring Friendli BYOK supported for `aider`; until then `plan` must reject that route for
  this agent rather than silently lose routing constraints.

`glm-5.2`, `kimi-k3`, and `opus-5` remain independently selectable. Engine support means command,
credentials, and model settings resolve correctly; it does not imply leaderboard presence or real
VM verification. None of these exact profile IDs has a directly reusable official row in the
inspected leaderboard data (older Opus/Kimi variants are different models).

## 7. Resume, retry, cancellation, and budget

### Native behavior

- A parseable existing `.aider.results.json` is skipped on continuation, regardless of pass/fail.
- Invalid JSON is rerun. A task interrupted before final result is rerun.
- At each fresh task execution editable files are restored from the source dataset. Within its two
  attempts, edited files and conversation state are retained.
- The chat history file may contain earlier partial text after an interrupted task, but a newly
  constructed Coder does not use that file as restored model conversation. Record this limitation.
- There is no native “rerun failed only” switch. It can be achieved only by deliberately removing
  selected result artifacts, which is destructive and must not happen implicitly on engine resume.

### Engine behavior

- Use a deterministic run-owned output directory and pass `--cont` on resumed execute stages.
  Completed task results are reused; missing/invalid tasks rerun according to native semantics.
- Preserve failed results on normal resume. Add no automatic retry beyond the official two tries.
- A future explicit `rerun-failed` operator action is out of the initial scope.
- Extend budget measurement through a parser callback/protocol in `run.process`, not Aider-specific
  conditionals in Harbor. The Aider implementation sums numeric `cost` fields from completed native
  results. Unknown costs remain unknown; the watchdog cannot guarantee a strict ceiling because an
  in-flight request may exceed the remaining budget.
- Before every task start the native runner cannot enforce the engine's per-task dollar limit;
  `per_task_cost_limit_usd` is unsupported by official Aider. Record it as not enforced for `aider`
  and rely on total budget monitoring. Do not claim parity with mini-swe-agent's hard argument.
- On budget trip, terminate the Docker process group, leave completed artifacts resumable, fail
  execute, and keep the VM lease. Operator may resume only after consciously increasing/accepting
  budget via a future immutable-spec policy; the current immutable spec means the practical action
  is cancellation/new run. Do not mutate resolved budget silently.
- Existing cancel should find the container through the run-directory bind mount; add a unique
  run label as a second ownership signal and test exact-run cleanup.

## 8. Dependency and packaging decision

Add optional dependency extra `aider` only for engine-side utilities that are truly required. The
native Aider package and dev dependencies live in the pinned Docker image; installing
`aider-chat` into the engine venv is unnecessary if command/model-settings generation stays in our
adapter. This avoids Python 3.13 engine dependency conflicts with the official Python 3.11 image.

Expected profile settings:

```yaml
plugin: aider_polyglot
harness: aider-native
dataset_id: Aider-AI/polyglot-benchmark@7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f
grading: aider-native-inline-tests
default_agent: aider
settings:
  dependency_extra: aider
  runner_version: 0.86.0
  runner_revision: a4be6ccd87ebaa59b361f3f028d116ce1761b626
  dataset_revision: 7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f
  dockerfile_sha256: 352813bb478c35b88981d03a23679b33f5d1a78871c53173a68b52422fe68e80
  attempts: 2
  test_timeout_seconds: 180
  edit_format: auto
```

If an empty optional extra is invalid/unhelpful, teach remote deploy that `dependency_extra` may be
absent and do a frozen base sync; do not add a fake dependency. `uv.lock` changes only if a real
engine dependency is added. Hatch already includes package files below `src/agent_benchmark`, but
wheel inspection must verify the YAML, catalog, and attribution asset.

## 9. Files to add or modify after approval

Before each implementation group, report the exact files, reason, behavior change, and validation.

### Add

- `eval_engine/src/agent_benchmark/benchmarks/aider_polyglot/__init__.py`
- `eval_engine/src/agent_benchmark/benchmarks/aider_polyglot/benchmark.py`
- `eval_engine/src/agent_benchmark/benchmarks/aider_polyglot/pool.py`
- `eval_engine/src/agent_benchmark/benchmarks/aider_polyglot/results.py`
- `eval_engine/src/agent_benchmark/benchmarks/aider_polyglot/assets/catalog.json`
- `eval_engine/src/agent_benchmark/benchmarks/aider_polyglot/assets/ATTRIBUTION.md`
- `eval_engine/src/agent_benchmark/harnesses/aider_native.py`
- `eval_engine/src/agent_benchmark/agents/aider.py`
- `eval_engine/src/agent_benchmark/config/agents/aider.yaml`
- `eval_engine/src/agent_benchmark/config/benchmarks/aider-polyglot.yaml`
- `eval_engine/tests/fixtures/aider_polyglot/` with small synthetic/raw result fixtures, no copied
  upstream tests unless licensing is explicitly preserved
- focused tests named for catalog/pool, native command, agent invocation, results, resume/budget,
  compatibility, report, and packaging

### Modify minimally

- `benchmarks/__init__.py`: local plugin dispatch only; no new central registry.
- `harnesses/__init__.py`: dispatch `aider-native`.
- `agents/base.py`: if needed, add a typed native invocation field without Aider-specific names.
- `config/schema.py` and `config/loader.py`: compatibility validation and any resolved runner
  provenance needed for immutable plans. Keep agent out of model profiles.
- `run/worker.py`: continue dispatching the profile-selected harness; no Harbor assumption.
- `run/process.py`: generic pluggable measured-cost reader.
- `run/pipeline.py` and `run/result.py`: merge benchmark-provided aggregate metrics while keeping
  existing generic reports stable.
- `run/remote.py`: optional dependency-extra handling and exact container cancellation ownership.
- `eval_engine/pyproject.toml` / `uv.lock`: only if a real extra/dependency is necessary.
- `eval_engine/README.md`: supported benchmark/agent/model lists, defaults, compatibility and
  comparability matrix, provider restrictions, smoke commands, and actual verification status.

Reuse `BenchmarkPlugin`, `AgentAdapter`, `HarnessAdapter`, pool JSON conventions, `run_logged`, SSH
lease/deploy/collect/checksum/cancel lifecycle, `TaskResult`, and RunStore stage resume. Do not add
`core`, `application`, `runtime`, `orchestration`, a public `--harness`, or a central registry.

## 10. Test and verification plan

No test should call a model or require the real VM.

1. Catalog: exact SHA, 225 unique IDs, language counts 26/39/47/49/34/30, reversible source paths,
   and catalog/task digests.
2. Pool: full default, deterministic random, deterministic balanced language sample, boundaries,
   invalid strategies, no duplicate IDs.
3. Config/CLI: `plan` resolves `aider-polyglot` + default `aider`; `--agent aider`; explicit
   rejection of unsupported agents; all three model profiles remain independent; no model YAML has
   agent data; harness remains non-user-selectable.
4. Pinning/prepare: mocked git/docker commands contain full SHAs, reject mismatched commits/file
   hashes, idempotent cache marker, exact selected task materialization, no packaged dataset tests.
5. Native command: image/run labels/mounts, `--exercises-dir`, workers, model, two tries, reasoning,
   auto/explicit edit format, no unseeded `--num-tests`, secret redaction.
6. Credentials: OpenRouter and Anthropic environment mapping; generated model settings; exact
   Friendli provider body or explicit rejection until validated.
7. Lifecycle: execute performs native inline tests once; grade only validates artifacts; no test
   command in grade.
8. Normalization fixtures: first-try pass, second-try pass, two failures, timeout, malformed edit,
   syntax error, task exception, malformed JSON, missing result, explicit zero vs absent cost/tokens,
   duplicate/unknown ID.
9. Aggregate: official completed-denominator pass rates match native semantics; conservative
   pool-denominator rates; language rates; well-formed metric; `resolved == pass_at_2`.
10. Resume: completed results skipped, interrupted/invalid task rerun, failed valid task retained,
    native output directory reused.
11. Budget/cancel: generic Aider cost reader, total threshold termination, unknown cost behavior,
    redaction, exact run-owned container cleanup.
12. Existing regression: full pytest suite, all SWE-bench and Terminal-Bench tests, both existing
    agents across both existing benchmarks, result/report fixtures.
13. Quality/package: `ruff check`, `ruff format --check`, full `pytest`, build wheel, inspect wheel
    for benchmark YAML/catalog/attribution and absence of exercises/tests, install wheel in a clean
    temporary venv, run profile listing and mocked `plan`.

Required local commands after implementation:

```bash
cd eval_engine
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
unzip -l dist/*.whl
```

## 11. VM smoke gate (only after mocked implementation passes)

Do not run this without separate approval. First verify `agent-bench remote doctor`, Docker access,
>=16 GiB RAM, >=30 GiB free disk, and outbound GitHub/container/package/model access.

Proposed first paid smoke test (one deterministic task, one worker, explicit total budget):

```bash
cd eval_engine
uv run agent-bench plan \
  --benchmark aider-polyglot \
  --model glm-5.2 \
  --agent aider \
  --reasoning-effort xhigh \
  --provider openrouter \
  --workers 1 \
  --budget-usd 5 \
  --sampling random \
  --size 1

uv run agent-bench run \
  --benchmark aider-polyglot \
  --model glm-5.2 \
  --agent aider \
  --reasoning-effort xhigh \
  --provider openrouter \
  --workers 1 \
  --budget-usd 5 \
  --sampling random \
  --size 1
```

If the adapter's total-only budget policy requires making the currently mandatory per-task option
explicit, include `--per-task-cost-limit-usd 5` but document that Aider does not enforce it. Do not
use Friendli routing in the first smoke test until the mocked provider-body assertion passes.

Local bundle is `eval_engine/runs/<run-id>/`; durable streaming logs are under `logs/`, raw native
trees and summaries under `artifacts/aider-native/`, normalized rows under `results/`, and final
aggregates under `report/`. After the user supplies artifacts, inspect task isolation, selected
agent/model, prompt and initial files, final diff, both outcomes, normalized result, aggregate,
tokens/cost, resume state, image/toolchain provenance, and cleanup. Only that exact combination may
be marked VM-verified.

After a 1-task success, request separate approval for 3 tasks before any full run. A full run at one
worker has historically varied by model from roughly seconds to several minutes per case; published
leaderboard examples imply about 1–28 hours of model-turn time for 225 tasks before build/cache
overhead. Cost examples span below $1 to over $100 depending on model. There is no defensible cost
estimate for the repository's exact GLM-5.2, Kimi-K3, or Opus-5 profiles until measured smoke data
exists. Use the explicit budget as the operational ceiling, allowing for in-flight overshoot.

## 12. README matrix change after implementation

Add Aider Polyglot to the benchmark list as 225 tasks, default agent `aider`, sampling `random`,
`language`, or full, and inline native tests with artifact-validation grade. Split the compatibility
table into three independent labels:

- **Supported**: engine command/config/result path is implemented and tested.
- **Officially comparable**: full pinned Aider Coder + Aider Benchmark Harness method; samples and
  non-Aider agents are not comparable.
- **VM verified**: only exact combinations actually run on the fixed VM.

Initial expected matrix:

| Benchmark | mini-swe-agent | terminus-2 | aider |
|---|---|---|---|
| SWE-bench Verified | Supported; VM verified default | Supported; command-tested | Unsupported initially |
| Terminal-Bench 2.1 | Supported; command-tested | Supported; VM verified default | Unsupported initially |
| Aider Polyglot | Unsupported initially | Unsupported initially | Supported default; officially comparable only for full compliant runs; mocked until smoke |

The provider section must state which Aider combinations are command-tested and which are rejected.
The actual-verification section must not generalize one successful model/provider/sample to all
models, providers, agents, or full leaderboard comparability.

## 13. Risks and non-goals

- **Highest regression risk:** making common execute/report/budget code assume Aider artifact
  shapes. Mitigation: harness dispatch stays profile-owned; parsing stays in the benchmark package;
  cost reading is a generic callback; run all existing tests.
- **Comparability risk:** changing runner, Aider version, edit format, attempts, dataset, test
  environment, or denominator. Mitigation: immutable provenance and explicit comparability field.
- **Reproducibility risk:** upstream Dockerfile contains floating package/toolchain inputs and native
  task order is unseeded. Mitigation: exact pool materialization, source hashes, observed image and
  toolchain metadata; do not overclaim bit reproducibility.
- **Resume risk:** native valid failures are skipped and an interrupted task starts a new coder.
  Mitigation: test/document exact behavior and preserve raw state.
- **Cost risk:** no native hard budget and in-flight overshoot. Mitigation: measured completed-task
  watchdog, tiny smoke, one worker, explicit budget.
- **Licensing risk:** no dataset-wide root license. Mitigation: no exercise redistribution; preserve
  attribution and source notices.
- **Contamination risk:** public Exercism-derived tests may be in model training. Mitigation:
  disclose it in reports/README.

Out of scope remains an automation agent, UI, multi-VM scheduler, concurrent runs, leaderboard site
clone, unrelated engine redesign, and other benchmarks.

## 14. External implementation survey

Searches covered Aider Polyglot automation, Docker, OpenRouter/custom models, result parsing,
resume/failed tasks, CI, non-Aider agents, and harness integration. Public examples overwhelmingly
invoke the same official `benchmark.py`; they vary model server, language filters, threads, tries,
or patched prompts. No maintained independent CI-grade harness/result parser was found that should
replace the official source.

Examples reviewed:

- Official Aider benchmark README/source and official leaderboard (authoritative behavior).
- A Hugging Face model discussion running the official command and publishing native YAML metrics:
  [Qwen3-Coder-REAP-25B-A3B discussion](https://huggingface.co/cerebras/Qwen3-Coder-REAP-25B-A3B/discussions/1).
- A custom MISRA experiment that pins an Aider commit but patches runner and dataset, demonstrating
  that such scores are modified-benchmark results rather than direct official rows:
  [experiment gist](https://gist.github.com/paraemsi/6ac59bf2d3583f3a16efaa0798e146fc).
- Local model experiments using the official Docker/runner with custom endpoints and model settings;
  useful for provider invocation only, not canonical semantics.

External examples do not add reliable exact-task selection, deterministic random sampling, strict
budgeting, stable image digests, or generic non-Aider agent support. Those gaps must be addressed in
the engine wrapper while preserving the pinned native attempt loop.

## 15. Approval checkpoint

Implementation must not begin until the user explicitly approves this plan. Approval authorizes
repository edits and mocked/local validation only. It does not authorize a real VM run, paid model
calls, publishing an image, pushing Git commits, or changing/removing the user's existing dirty
worktree files.

At research time the pre-existing dirty files were:

- modified `.gitignore`
- untracked `docs/EXPERIMENT_ANALYSIS_NOTEBOOK_PLAN.md`

They must remain untouched and excluded from any later Aider integration commit.
