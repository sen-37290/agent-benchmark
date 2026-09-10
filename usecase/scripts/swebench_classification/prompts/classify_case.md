Classify this SWE-bench task by the work a coding model must perform, using only the frozen
solver-demand codebook.

Read the SOLVER-FACING EVIDENCE first, including the `problem_statement`. Judge the solver's
starting information, required interpretation, repository learning, diagnosis, implementation,
and verification without assuming access to the solution.

Then use the AUDITOR-ONLY EVIDENCE—the PR, gold patch, test patch, and test lists—to confirm the
actual cause and solution. Do not turn their literal defect mechanism into the task class, and do
not infer easy diagnosis from a small patch.

Choose one primary solver-demand class. If no primary class fits, set classification_status to
NEW_CLASS and primary_solver_demand_class to null. If the evidence cannot support a reliable
decision, set classification_status to UNCERTAIN and primary_solver_demand_class to null. Every
important judgment must have a short exact quotation from its declared evidence source.
