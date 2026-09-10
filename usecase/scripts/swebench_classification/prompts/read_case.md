You are analyzing a SWE-bench Verified task to determine what work and abilities a coding model
would need to solve it. Do not classify the literal defect mechanism. The result must describe the
problem-solving demand.

The input contains two evidence groups.

SOLVER-FACING EVIDENCE is what defines the task before the solution is known. It includes the
cleaned original GitHub issue, SWE-bench `problem_statement`, and `hints_text`. You MUST read the
problem_statement, even when it overlaps the issue. Use this group to judge what must be
understood, what is ambiguous, what repository knowledge must be acquired, and what diagnostic
work a solver must perform.

AUDITOR-ONLY EVIDENCE includes the original PR, gold patch, test patch, and test lists. Use it to
confirm the actual root cause, solution scope, and verification target. Do not act as though a
solver begins with the PR or gold patch. A small gold patch does not imply that diagnosis was easy.

For every case reconstruct the solver's work:
1. Understand the requested or expected behavior.
2. Identify the repository concepts and relationships that must be learned.
3. Locate and confirm the cause.
4. Design and implement the change.
5. Verify correctness and avoid regressions.

Rate interpretation, diagnosis, implementation, and verification independently as LOW, MEDIUM,
or HIGH. Propose reusable capability phrases describing solver actions or reasoning, never names
for the code defect. Every substantive conclusion needs a short exact quotation from its declared
source. Mark unsupported information as uncertain rather than inventing it.

