# SWE-bench classification

The script joins the raw GitHub issue/PR text to SWE-bench Verified and classifies each task by the
work and capabilities a coding model needs to solve it. It does not use benchmark model results.

```bash
cd usecase/scripts/swebench_classification
python run.py
```

Before the first run, set `input_xlsx` in `config.yaml` to your copy of the scraped
workbook. Everything the pipeline writes lands in `outputs/`, which is not tracked in
git.

`run.py` installs missing modules into the active Python environment with `uv`. If
`OPENAI_API_KEY` is not set, it asks for the key using hidden terminal input. The source XLSX is
read-only and never changed.

The solver-facing evidence explicitly includes the cleaned issue, `problem_statement`, and
`hints_text`. The PR, gold patch, and test patch are auditor-only evidence used to confirm the real
cause and solution without treating them as information available to the hypothetical solver.

The pipeline keeps the completed case analyses and derives task-shape profiles from their separate
interpretation, diagnosis, implementation, and verification ratings. It does not generate a
technical-domain taxonomy. If a previous run stopped after analyzing 150 cases, `prepare` resumes
from those files and cached responses instead of paying to analyze them again.

```bash
python run.py --phase validate
python run.py --phase prepare
```

For an API-free verification run:

```bash
pytest -q tests
```
