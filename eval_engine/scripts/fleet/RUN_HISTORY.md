# GPT/Fable benchmark run history

Last reconstructed: 2026-09-09. This is the durable lineage for the closed-model SWE-bench
Verified and Terminal-Bench 2.1 campaign. It records runs that were aborted or superseded as well
as the runs that contributed to the canonical aggregate; a row being present here does not mean
its result should be merged.

The source of truth for scores and task artifacts is
`~/results-archive/FINAL-AGGREGATED/` on `sen-agent-cyber-bench-vm-2`. Exact source-run membership
must be read from each aggregate's `aggregate-manifest.json` and `report/summary.json`. The local
Claude histories and fleet configuration explain why each rerun happened, while the remote
manifests decide what was ultimately used.

## Headline lineage

| Date (UTC) | Run or group | Configuration | What happened | Disposition |
|---|---|---|---|---|
| 2026-09-03 07:17 | Initial ten-run fleet | GPT-5.6 and Fable; provider-default effort | The first launch exposed Responses-vs-chat incompatibility, rejected request parameters, anonymous Docker Hub throttling, missing Compose v2, and CyberGym policy/refusal failures. Several rows were relaunched after fixes. | Infrastructure bring-up; not a single comparable final cohort. |
| 2026-09-03 07:49 | Corrected default-effort SWE runs | GPT-5.6/Fable SWE, effort omitted | GPT SWE completed at the provider default, which was later proven to be `medium`. Archived comparison scores were Sol 424/500, Terra 379/500, and Luna 347/500. | Preserved under `results-archive/full`; superseded for the intended high-effort study. |
| 2026-09-03 20:22–20:49 | Highest-effort relaunch waves | GPT SWE/TB initially requested `xhigh`; Fable requested `max` | Several launch waves were stopped or relaunched while timeout/cost settings were corrected. The GPT SWE `xhigh` attempts were scoreless; unlike `max` on LiteLLM 1.94.0, `xhigh` would have been transmitted. | Aborted/superseded. Keep for provenance, never merge blindly. |
| 2026-09-03 21:08–21:10 | Intended-max GPT SWE cohort | Sol `f459eb5e`, Terra `79f19609`, Luna `94509e6c`; requested `max` | LiteLLM 1.94.0 silently removed `reasoning_effort=max` from Responses requests. All 72,188 stored Responses objects across the three runs echoed `medium`. Raw scores before later infrastructure repair were 423, 386, and 356. | These are medium-effort results despite recording `max`; they require full replacement. |
| 2026-09-03 onward | Highest-effort Terminal cohort | GPT Terminal used supported `xhigh`; Fable used `max` | Terminal used chat completions, where GPT-5.6 tops out at `xhigh`; archived configs and a later wire probe confirmed it was transmitted. Fable's Anthropic path supports `max`. | Valid effort settings; repaired only for task-level failures described below. |

## Archived baseline and relaunch attempts

This table is the run-level inventory recovered from `results-archive/full` and the canonical
aggregate's per-run reports on vm-2. Zero-completion rows are real launch attempts, not missing
records. They are retained here because they explain the successive configuration corrections.

| Run ID | Effort | Recorded outcome | Disposition |
|---|---|---|---|
| `20260903T070209Z-sen-fable-5-1-terminal-bench-b3ee121a` | default | 0/2 completed | Bring-up attempt; superseded |
| `20260903T070554Z-sen-fable-5-1-terminal-bench-0a9787b2` | default | 0/2 completed | Bring-up attempt; superseded |
| `20260903T071103Z-sen-fable-5-1-terminal-bench-309ab3ed` | default | 0/2 completed, 2 errors | Bring-up attempt; superseded |
| `20260903T071618Z-sen-fable-5-1-terminal-bench-8d5202e2` | default | 0/2 completed | Bring-up attempt; superseded |
| `20260903T071754Z-sen-fable-5-1-terminal-bench-39ca8cfa` | default | 75/89 completed, 70 passed | Default-effort baseline; superseded |
| `20260903T071754Z-sen-gpt-5-6-sol-terminal-bench-b5bef577` | default | 83/89 completed, 69 passed | Default-effort baseline; superseded |
| `20260903T071754Z-sen-gpt-5-6-terra-terminal-bench-38620414` | default | 80/89 completed, 58 passed | Default-effort baseline; superseded |
| `20260903T071754Z-sen-gpt-5-6-luna-terminal-bench-18cc9f0b` | default | 82/89 completed, 54 passed | Default-effort baseline; superseded |
| `20260903T071759Z-sen-fable-5-1-swe-bench-2eb1ecc0` | default | 99/500 completed, 86 passed | Interrupted baseline; superseded |
| `20260903T074952Z-sen-fable-5-1-swe-bench-47020896` | default | 472/500 completed, 426 passed | Default-effort baseline; superseded |
| `20260903T074952Z-sen-gpt-5-6-sol-swe-bench-08033cde` | default (`medium`) | 500/500 completed, 424 passed | Preserved comparison baseline |
| `20260903T074952Z-sen-gpt-5-6-terra-swe-bench-21cb6749` | default (`medium`) | 473/500 completed, 379 passed | Preserved comparison baseline |
| `20260903T074952Z-sen-gpt-5-6-luna-swe-bench-53a15198` | default (`medium`) | 475/500 completed, 347 passed | Preserved comparison baseline |
| `20260903T203444Z-sen-fable-5-1-terminal-bench-0a90cd0c` | `max` | 0/89 completed | Highest-effort relaunch wave; superseded |
| `20260903T203444Z-sen-gpt-5-6-sol-terminal-bench-7c37b0d6` | `xhigh` | 0/89 completed | Highest-effort relaunch wave; superseded |
| `20260903T203444Z-sen-gpt-5-6-terra-terminal-bench-d7ba44cf` | `xhigh` | 0/89 completed | Highest-effort relaunch wave; superseded |
| `20260903T203444Z-sen-gpt-5-6-luna-terminal-bench-3e0a5b6e` | `xhigh` | 0/89 completed | Highest-effort relaunch wave; superseded |
| `20260903T204116Z-sen-fable-5-1-terminal-bench-a935198d` | `max` | 0/89 completed | Corrected relaunch wave; superseded |
| `20260903T204116Z-sen-gpt-5-6-sol-terminal-bench-53320585` | `xhigh` | 0/89 completed | Corrected relaunch wave; superseded |
| `20260903T204116Z-sen-gpt-5-6-terra-terminal-bench-8756c83b` | `xhigh` | 0/89 completed | Corrected relaunch wave; superseded |
| `20260903T204116Z-sen-gpt-5-6-luna-terminal-bench-962ef26c` | `xhigh` | 0/89 completed | Corrected relaunch wave; superseded |
| `20260903T204906Z-sen-fable-5-1-terminal-bench-1fe6a7c8` | `max` | Baseline artifacts retained; canonical aggregate selects its usable rows | Repaired below |
| `20260903T204906Z-sen-gpt-5-6-sol-terminal-bench-b71735d4` | `xhigh` | 83/89 completed, 74 passed | Repaired below |
| `20260903T204906Z-sen-gpt-5-6-terra-terminal-bench-0c4f6125` | `xhigh` | 77/89 completed, 59 passed | Repaired below |
| `20260903T204906Z-sen-gpt-5-6-luna-terminal-bench-54c276a8` | `xhigh` | 76/89 completed, 62 passed | Repaired below |
| `20260903T204908Z-sen-fable-5-1-swe-bench-73e5f31d` | `max` | 484/500 completed, 339 passed; 16 images missing | Repaired below |
| `20260903T210821Z-sen-gpt-5-6-sol-swe-bench-f459eb5e` | requested `max`, served `medium` | 500/500 completed, 423 passed | Must be fully replaced |
| `20260903T210917Z-sen-gpt-5-6-terra-swe-bench-79f19609` | requested `max`, served `medium` | 482/500 completed, 386 passed | Repaired for images, but must now be fully replaced |
| `20260903T211013Z-sen-gpt-5-6-luna-swe-bench-94509e6c` | requested `max`, served `medium` | 482/500 completed, 356 passed | Repaired for images, but must now be fully replaced |
| `20260903T223255Z-sen-fable-5-1-terminal-bench-7684e930` | `max` | 65-task no-timeout subset; rows retained in the aggregate. One refusal/rate-limit loop recorded 419,478,438 input tokens and `$5,019.44` for `dna-insert`, inflating the run's recorded cost to `$5,267.46`; this was accounting from repeated context, not a single provider charge of that size. | Repaired below |

One additional Terra Terminal launch, `20260903T202219Z-sen-gpt-5-6-terra-terminal-bench-cab4833b`,
was observed in the VM/session trace but has no surviving `summary.json` in the current archive. It
is therefore recorded as an aborted pre-`203444Z` attempt, not assigned a score.

## SWE-bench repair history

| Run ID | Scope and trigger | Change | Outcome / use |
|---|---|---|---|
| `20260905T025135Z-sen-gpt-5-6-terra-swe-bench-docker-timeout-cbea1259` | 18 Terra matplotlib tasks with no trajectory and $0 cost after `docker run` timed out while implicitly pulling multi-GB images | Pre-pull the pool's images with bounded concurrency; raise mini-swe-agent's Docker pull timeout | Merged into Terra, moving the canonical result from 386 to 401/500. |
| `20260905T030519Z-sen-gpt-5-6-luna-swe-bench-docker-timeout-0903dba8` | The same 18-task Docker-start failure class on Luna | Same image-cache repair | Merged into Luna, moving the canonical result from 356 to 368/500. |
| `20260905T030426Z-sen-fable-5-1-swe-bench-remove-cost-limit-1fbde7a5` | First Fable repair launch | Raised the official $3 task cap explicitly and enabled fallback instrumentation | Superseded by the corrected relaunch below. |
| `20260905T033647Z-sen-fable-5-1-swe-bench-remove-cost-limit-c4bb140e` | 139 tasks cut off by the flat $3 cap plus 16 Docker-timeout tasks | Deliberate $20/task override, bounded image pre-pull, Anthropic fallback insurance | Produced 125 usable results; the remaining 30 hit Anthropic account/API usage capacity. |
| `20260905T065507Z-sen-fable-5-1-swe-bench-api-limit-retry-30-ec3eea90` | The 30 empty-patch infrastructure losses from the preceding repair | Same model, effort, fallback, and task cap after a capacity probe | Merged; canonical Fable SWE became 489/500 (97.80%), all 500 graded. |

The original Fable SWE score was 339/500. Its 161 non-resolved rows were six genuine verifier
failures, 139 `$3` cost-cap terminations, and 16 Docker-start timeouts. The 67.8% result therefore
measured the old harness limits, not a clean model run.

## Terminal-Bench repair history

| Run ID | Scope and trigger | Change | Outcome / use |
|---|---|---|---|
| `20260904T195340Z-sen-fable-5-1-terminal-bench-retry-24-810bfa95` | 24 Fable tasks lost to credit exhaustion, input-token rate limits, or connection timeouts | Added $20/task and $600 run caps, widened transient retries, removed the Harbor agent deadline | Exposed the refusal loop: 13/24 still failed because Fable returned HTTP 200 with empty refusal content. |
| `20260905T005442Z-sen-fable-5-1-fallback-to-opus-terminal-bench-smoke-8ad78a61` | One known cyber refusal | Anthropic server-side `fallbacks: default` plus a fallback ledger | Smoke gate for the full fallback run. |
| `20260905T010914Z-sen-fable-5-1-fallback-to-opus-terminal-bench-881450e4` | The 24-task repair pool with refusal handling | Fable remained primary; refusals were served by Opus 4.8/5 and recorded | Ten previously missing verdicts were later selected: nine passes and one genuine failure. Fallback answers intentionally count as system results. |
| `20260905T011957Z-sen-gpt-5-6-sol-fallback-terminal-bench-c8769367` and `...-365dd533` | Sol's two `cyber_policy` tasks | Client-side ladder Sol retry → Terra → Luna, with ledger | Corrected/redeployed after launch-env and stale-run-ID bugs; selected results are tracked by the aggregate manifest. |
| `20260904T220720Z-sen-gpt-5-6-sol-terminal-bench-retry-ea771614` | Six Sol timeout/policy cases | 6× agent deadline and transient retry fixes | Bounded repair pass; one remaining timeout later went to the no-timeout tail. |
| `20260905T004140Z-sen-gpt-5-6-terra-terminal-bench-retry-dc1e5f18` | Eleven Terra timeout/tmux cases | 6× deadline | Three remaining deadline/session cases later went to the no-timeout tail. |
| `20260905T004037Z-sen-gpt-5-6-luna-terminal-bench-retry-063a93ba` | Eight true Luna failures; four additional apparent failures were recovered from verifier truth without rerunning | 6× deadline and corrected normalization | Two remaining timeout cases later went to the no-timeout tail. |
| `20260905T150503Z-sen-fable-5-1-remove-cost-limit-terminal-bench-retry-10-566682b5` | Ten Fable tasks still cut off by cost/deadline behavior | Removed practical task/run caps; fallback enabled | Did not clear the long silent-response failures. |
| `20260905T183632Z-sen-fable-5-1-long-request-timeout-terminal-bench-retry-6-eeb4e9de` | Six-task unresolved tail | Increased the non-streaming read timeout to 3600 seconds | Stopped after 108 minutes with 57 steps and zero completed tasks; longer silence made dead sockets more expensive. |
| `20260905T202924Z-sen-fable-5-1-streaming-terminal-bench-retry-6-5befc145` | Same six tasks | Stream LiteLLM responses internally, reassemble before Harbor, use an inter-chunk idle timeout, preserve cost/fallback telemetry | Five passes and one genuine failure; merged. |
| `20260906T070219Z-sen-fable-5-1-streaming-terminal-bench-gap-18-6647325c` | Eighteen rows believed to lack verdicts | Streaming, fallback, no practical caps/deadline | Stopped after discovering ten already had valid fallback-run verdicts. Superseded by gap-8. |
| `20260906T071809Z-sen-fable-5-1-streaming-terminal-bench-gap-8-c9477473` | Eight truly unmeasured Fable tasks | Same repaired streaming/fallback configuration | Five passes, three genuine verifier failures; merged. Fable Terminal finished at 82/89 (92.13%) with 89/89 verdicts. |
| `20260905T191123Z-sen-gpt-5-6-sol-terminal-bench-no-timeout-retry-1-3e9d5f46` | One remaining Sol timeout | No Harbor agent deadline | Final Sol tail repair. |
| `20260905T185318Z-sen-gpt-5-6-terra-terminal-bench-no-timeout-retry-3-cde71aba` | Three remaining Terra timeout/session rows | No deadline; isolated target | Final Terra tail repair. |
| `20260905T185318Z-sen-gpt-5-6-luna-terminal-bench-no-timeout-retry-2-1faa5c60` | Two remaining Luna timeout rows | No deadline; isolated target | Final Luna tail repair. |

The resulting canonical Terminal scores are Fable 82/89, Sol 78/89, Terra 66/89, and Luna
67/89. GPT used `xhigh`, the maximum accepted by its chat-completions path; these runs are not
affected by the Responses-API `max` bug.

## Current correction: true-max GPT SWE

LiteLLM issue #38084 was fixed in 1.100.0. Version 1.94.0 accepted `max` in our resolved spec but
removed it while translating a Responses request, causing the provider to use `medium`. The next
cohort is a full replacement rather than a failed-task patch:

| Experiment / VM | Model | Key source | Scope | State |
|---|---|---|---:|---|
| `sen-gpt-5-6-sol-swe-bench-litellm-fix-max` | `gpt-5-6-sol` | `SEN_GPT_5_6_SOL_SWE_BENCH_LITELLM_FIX_MAX` | 500 | Executing as `20260909T070133Z-sen-gpt-5-6-sol-swe-bench-litellm-fix-max-0768b836` |
| `sen-gpt-5-6-terra-swe-bench-litellm-fix-max` | `gpt-5-6-terra` | `SEN_GPT_5_6_TERRA_SWE_BENCH_LITELLM_FIX_MAX` | 500 | Executing as `20260909T070125Z-sen-gpt-5-6-terra-swe-bench-litellm-fix-max-fc92adf2` |
| `sen-gpt-5-6-luna-swe-bench-litellm-fix-max` | `gpt-5-6-luna` | `SEN_GPT_5_6_LUNA_SWE_BENCH_LITELLM_FIX_MAX` | 500 | Executing as `20260909T070133Z-sen-gpt-5-6-luna-swe-bench-litellm-fix-max-34229c79` |

Launch gates: LiteLLM must be exactly 1.100.0, all 500 task/grader images must be present before
the first model request, and a Responses-API preflight must echo `reasoning.effort=max`. After
completion, add each run ID, timestamps, score, cost, retry census, effort census, and archive
location here. Do not overwrite the medium-effort aggregate until that validation is complete.

The first 2026-09-09 controller attempt on each fresh VM pulled and inspected all 500 images,
then stopped before creating a run: the effort probe sent the fleet profile alias
(`openai/gpt-5-6-*`) instead of the profile's provider model (`openai/gpt-5.6-*`) and received a
non-retryable `NotFoundError`. Commit `b6cd786` made the probe resolve the packaged model profile.
After redeployment, the retained caches were inspected 500/500 and three real Responses calls on
LiteLLM 1.100.0 each echoed `reasoning.effort=max`; only then were the run IDs above created. At
launch, every run entered the execute stage with 30 workers and the official $3 per-task cap. An
initial 2026-09-09T07:05Z census through the real mini-swe-agent path found `max` in 206/206 stored
Sol responses, 178/178 Terra responses, and 397/397 Luna responses, with no stored `medium` effort.

## Reconstruction references

- Claude session `8c4fb3ce-1f11-4d65-93a8-fb0abeb0f0bb`: initial fleet creation and launch.
- Sessions `8d07ed70-d7cd-465c-b8b9-b048cd9f43e0`,
  `3c71b5c4-99ec-42ae-89e1-f1f4c87e69b3`, and
  `2f29f5a9-f2b5-4e1a-b71e-e3576e646cc0`: Fable retry, refusal, and fallback sequence.
- Session `b27e9d63-d810-439e-89b8-9ce7dac7d139`: GPT client-side policy fallback.
- Session `a719ecbe-da5d-4cc6-9566-be4c3fc6ca05`: SWE failure audit, image pre-pull, and cap override.
- Session `3eb3d0d7-b1dd-4da3-9068-289536f8bb88`: streaming repair and final aggregation.
- Session `488fd984-62ee-4584-8ef5-4de153fb626e`: proof that requested `max` was served as `medium`.
- Git commits `5b3750e`, `79003ac`, `44f8b09`, `a4268c6`, `5bdfc51`, and `cdf71a7`.
