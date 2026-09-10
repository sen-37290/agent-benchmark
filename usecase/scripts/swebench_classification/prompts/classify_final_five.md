Classify this SWE-bench task into exactly one of the five categories below.

Judge the category by the dominant work needed to understand and solve the task. Read the problem
statement first, then use the gold patch and tests to confirm the actual defect and solution.

Return:

1. `quote`: one short, exact, verbatim quotation copied from the supplied evidence. Choose the
   passage that most strongly supports the classification. Do not paraphrase it and do not add an
   ellipsis or source label. When `VERBATIM QUOTE CANDIDATES` are supplied, copy exactly one
   candidate without its list marker.
2. `reason`: a concise explanation of why the quotation and confirmed solution fit the selected
   category better than the other categories.
3. `classification`: exactly one of the five category codes.

The five categories are exhaustive. Always choose the closest category; there is no empty,
uncertain, other, or new-class response.

- `DATA_FIDELITY_PROBLEMS`: The main challenge is preserving or producing the correct semantic
  value, representation, type, identity, metadata, mathematical result, or conversion across
  operations or boundaries.
- `TRACING_AND_OBSERVABILITY_PROBLEMS`: The main challenge is locating the faulty condition, state
  transition, call path, lifecycle stage, delegation point, or update point and placing the fix at
  the correct point in that flow.
- `RENDERING_AND_VISUAL_PROBLEMS`: The requested result is principally formatted text, generated
  output, display state, layout, plotting behavior, documentation rendering, or another directly
  observable presentation artifact.
- `COMPATIBILITY_PROBLEMS`: The main challenge is preserving established public behavior or making
  related APIs, options, versions, aliases, defaults, and extension participants behave
  consistently.
- `PARSING_PROBLEMS`: The main challenge is correctly interpreting structured input, tokens, names,
  paths, syntax, source-like forms, delimiters, casing, or grammar boundaries.
