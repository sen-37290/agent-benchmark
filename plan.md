we're going to run all these experiments on vm

| Experiment label                   | 
| ---------------------------------- | 
| `sen-gpt-5-6-sol-swe-bench`        | 
| `sen-gpt-5-6-terra-swe-bench`      | 
| `sen-gpt-5-6-luna-swe-bench`       | 
| `sen-fable-5-1-swe-bench`          | 
| `sen-gpt-5-6-sol-terminal-bench`   | 
| `sen-gpt-5-6-terra-terminal-bench` | 
| `sen-gpt-5-6-luna-terminal-bench`  | 
| `sen-fable-5-1-terminal-bench`     | 
| `sen-gpt-5-6-sol-cybergym-bench`   | 
| `sen-fable-5-1-cybergym-bench`     | 


### 1. VM capacity you need

current vm status

it's all in gcp-test-501403 (us-central1-a)

| vm name | experiment label |
| --- | --- |
| sen-agent-cyber-bench-vm | sen-gpt-5-6-sol-cybergym-bench | # this vm has stored cache of cybergym data - use it wisely
| sen-agent-cyber-bench-vm-2 | sen-fable-5-1-cybergym-bench | # this vm has stored cache of cybergym data - use it wisely
| sen-agent-bench-vm | sen-gpt-5-6-sol-swe-bench |
| sen-gpt-5-6-terra-swe-bench | sen-gpt-5-6-terra-swe-bench |
| sen-gpt-5-6-luna-swe-bench | sen-gpt-5-6-luna-swe-bench |
| sen-fable-5-1-swe-bench | sen-fable-5-1-swe-bench |
| sen-gpt-5-6-sol-terminal-bench | sen-gpt-5-6-sol-terminal-bench |
| sen-gpt-5-6-terra-terminal-bench | sen-gpt-5-6-terra-terminal-bench |
| sen-gpt-5-6-luna-terminal-bench | sen-gpt-5-6-luna-terminal-bench |
| sen-fable-5-1-terminal-bench | sen-fable-5-1-terminal-bench |

---

## 2. cost protection
because the openai and claude api key doesn't limit the cost for us - we need to limit ourselves in our pipeline

I remember to have budget limit per task and total cost
- but verify it applies to all of the benchmarks (especially cyber gym)
- If there isn't a limit logic for cost per task and total cost, implement it
- so we need to apply max limit cost per task following these value: 

### per-task cap

| Experiment                         | Tasks | `max_cost_per_task` |
| ---------------------------------- | ----: | ------------------: |
| `sen-gpt-5-6-sol-swe-bench`        |   500 |           **$5** |
| `sen-gpt-5-6-terra-swe-bench`      |   500 |           **$5** |
| `sen-gpt-5-6-luna-swe-bench`       |   500 |           **$5** |
| `sen-fable-5-1-swe-bench`          |   500 |           **$5** |
| `sen-gpt-5-6-sol-terminal-bench`   |    89 |           **$5** |
| `sen-gpt-5-6-terra-terminal-bench` |    89 |           **$5** |
| `sen-gpt-5-6-luna-terminal-bench`  |    89 |           **$5** |
| `sen-fable-5-1-terminal-bench`     |    89 |           **$5** |
| `sen-gpt-5-6-sol-cybergym-bench`   |   300 |          **$50** |
| `sen-fable-5-1-cybergym-bench`     |   300 |          **$50** |

When a single task hits this threshold:

```text
stop task
→ mark COST_LIMIT
→ save trajectory/logs
→ continue with next task
```

Don't kill the whole benchmark because one pathological task became expensive.

---

## experiment-level hard cap

also enforce existing max experiment costs

| Experiment     | Experiment hard cap |
| -------------- | ------------------: |
| Sol SWE        |            **$500** |
| Terra SWE      |            **$500** |
| Luna SWE       |          **$500** |
| Fable SWE      |            **$500** |
| Sol Terminal   |            **$500** |
| Terra Terminal |            **$500** |
| Luna Terminal  |          **$500** |
| Fable Terminal |            **$500** |
| Sol CyberGym   |          **$5000** |
| Fable CyberGym |          **$5000** |

# 5. Your concurrency architecture

### IMPORTANT RULE
I need a central controller that monitor each benchmark experiment(=monitor each vm)
but record each benchmarks in each vms
and eventhough we stop each experiment in mid-way,
1. It must run full pipeline: grading, record all the logs (eventhough it was stopped or failed, or only partial run was stopped all of the records must be recorded - e.g., all of the trajectory.json must be recorded even if the experiment was stopped midway)
2. DO NOT CLEAN-UP, the pipeline has automatic process of clean-up but don't. we need to cache all of the results, don't delete it. EVER. until I command so.
3. BUT exclude the process of pulling the results to local, my local computer don't have the capacity of storing all of those results.

```text
                    ┌──────────────────────┐
                    │ Experiment Controller│
                    │                      │
                    │ start / stop / status│
                    │ budgets / VM mapping │
                    └──────────┬───────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
 ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
 │ n2-standard-32  │   │ n2-standard-32  │   │ n2-standard-64  │
 │ Sol SWE         │   │ Terra SWE       │   │ Sol CyberGym    │
 │                 │   │                 │   │                 │
 │ benchmark runner│   │ benchmark runner│   │ benchmark runner│
 └────────┬────────┘   └────────┬────────┘   └────────┬────────┘

```

The important thing is that the **VM is an execution worker, not the source of truth**.

If a VM crashes, you should still know:

```text
experiment
tasks completed
tasks failed
last task
money spent
remaining budget
results location
```

---

# 6. What I would monitor concurrently

I'd create one central status view looking roughly like this:

| Experiment   | Status     |    Done | Running | Failed | Cost |   Cap | % Budget |
| ------------ | ---------- | ------: | ------: | -----: | ---: | ----: | -------: |
| Sol SWE      | 🟢 Running | 231/500 |       1 |      4 | $172 |  $415 |      41% |
| Terra SWE    | 🟢 Running | 198/500 |       1 |      2 |  $87 |  $224 |      39% |
| Luna SWE     | 🟢 Running | 287/500 |       1 |      1 |  $11 | $22.4 |      49% |
| Fable SWE    | 🟢 Running | 204/500 |       1 |      6 | $190 |  $453 |      42% |
| Sol Terminal | 🟢 Running |   41/89 |       1 |      3 |  $82 |  $189 |      43% |
| …            |            |         |         |        |      |       |          |

---

# 7. and also I should be easy to go in each vm

so tell me about the terminal commands
sometimes monitoring the view isn't enough 
I need to jump into each vm.
tell me terminal commands that I can use to inspect each experiment

---

I separated api key per each experiment.
so updated env looks like this

SEN_GPT_5_6_SOL_SWE_BENCH=
SEN_GPT_5_6_TERRA_SWE_BENCH=
SEN_GPT_5_6_LUNA_SWE_BENCH=
SEN_FABLE_5_1_SWE_BENCH=

SEN_GPT_5_6_SOL_TERMINAL_BENCH=
SEN_GPT_5_6_TERRA_TERMINAL_BENCH=
SEN_GPT_5_6_LUNA_TERMINAL_BENCH=
SEN_FABLE_5_1_TERMINAL_BENCH=

SEN_GPT_5_6_SOL_CYBERGYM_BENCH=
SEN_FABLE_5_1_CYBERGYM_BENCH=


and I mapped the vm to the experiment label in he env like this:

SEN_GPT_5_6_SOL_CYBERGYM_BENCH_VM=SEN_AGENT_CYBER_BENCH_VM
SEN_FABLE_5_1_CYBERGYM_BENCH_VM=SEN_AGENT_CYBER_BENCH_VM_2

SEN_GPT_5_6_SOL_SWE_BENCH_VM=SEN_AGENT_BENCH_VM
SEN_GPT_5_6_TERRA_SWE_BENCH_VM=SEN_GPT_5_6_TERRA_SWE_BENCH
SEN_GPT_5_6_LUNA_SWE_BENCH_VM=SEN_GPT_5_6_LUNA_SWE_BENCH
SEN_FABLE_5_1_SWE_BENCH_VM=SEN_FABLE_5_1_SWE_BENCH

SEN_GPT_5_6_SOL_TERMINAL_BENCH_VM=SEN_GPT_5_6_SOL_TERMINAL_BENCH
SEN_GPT_5_6_TERRA_TERMINAL_BENCH_VM=SEN_GPT_5_6_TERRA_TERMINAL_BENCH
SEN_GPT_5_6_LUNA_TERMINAL_BENCH_VM=SEN_GPT_5_6_LUNA_TERMINAL_BENCH
SEN_FABLE_5_1_TERMINAL_BENCH_VM=SEN_FABLE_5_1_TERMINAL_BENCH


---

Most Important 
all the running scripts must be runned end to end automatically within EACH VM.
I need to close my laptop, so I can't run the scripts in local
for each benchmark experiment - make an end to end script (it'll vary by benchmarks; prepare data -> generation = model solving each problem -> record all the results -> grading) # don't clean-up
that runs in each vm 
so in vm it runs automatically

but the monitoring should be able to be in local.
I'll close my laptop, but If I open it - and run somekind of script - it'll connect to each vm's and show the monitor.