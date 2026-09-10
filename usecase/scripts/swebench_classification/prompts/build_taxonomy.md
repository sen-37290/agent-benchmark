Build a solver-demand codebook from the supplied case analyses. The codebook exists to group tasks
by what differentiates coding-model success, not by the literal bug found in the patch.

Create 6-12 broad primary classes. Each class must:
- describe a reusable solving challenge;
- state the solver actions it requires;
- be motivated by at least three supplied cases;
- have boundaries that distinguish it from neighboring classes.

Reject class concepts that merely restate a concrete root cause, API option, sentinel, exception,
formatter, attribute lookup, or other patch-specific mechanism. Those details belong in case
summaries, not in the taxonomy. Prefer fewer broad classes over one class per bug pattern.
