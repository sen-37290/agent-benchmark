"""Finalize the five-class taxonomy by reclassifying only out-of-taxonomy v0 cases."""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from pipeline import (
    CLASSIFICATION_FIELDS,
    CODEBOOK_FIELDS,
    PipelineError,
    codebook_rows_to_payload,
    load_config,
    read_csv,
    validate_codebook_payload,
    write_csv,
)


VERSION = "v1-final-five"
FINAL_CODES = {
    "DATA_FIDELITY_PROBLEMS",
    "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "RENDERING_AND_VISUAL_PROBLEMS",
    "COMPATIBILITY_PROBLEMS",
    "PARSING_PROBLEMS",
}

FORCED_GUIDANCE = """

FINAL FIVE-CLASS TAXONOMY RULES:
- You must classify every case into exactly one of the five supplied primary classes.
- NEW_CLASS and UNCERTAIN are not available in this final consolidation pass.
- Choose the closest class by the dominant work needed from the solver, even if the fit is imperfect.
- Use TRACING_AND_OBSERVABILITY_PROBLEMS for localized implementation defects when the main work is
  locating the faulty condition, state transition, call path, or update point.
- Use DATA_FIDELITY_PROBLEMS when correctness depends mainly on preserving or producing the right
  semantic value, representation, type, identity, mathematical result, or conversion.
- Use COMPATIBILITY_PROBLEMS when the main concern is preserving established public behavior or
  making related APIs and extension participants behave consistently.
- Use PARSING_PROBLEMS when structured input, names, tokens, paths, syntax, or source-like forms are
  interpreted incorrectly.
- Use RENDERING_AND_VISUAL_PROBLEMS when the requested result is principally formatted text,
  generated output, display state, layout, or another directly observable presentation artifact.
"""

# Human consolidation of the 31 v0 records that were outside the final five classes.
# This is intentionally explicit so the final taxonomy can be audited without another API call.
FINAL_REMAP = {
    "django__django-11551": "COMPATIBILITY_PROBLEMS",
    "django__django-16485": "RENDERING_AND_VISUAL_PROBLEMS",
    "pytest-dev__pytest-7982": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "sympy__sympy-13757": "COMPATIBILITY_PROBLEMS",
    "sympy__sympy-15875": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-17318": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "sympy__sympy-19954": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "astropy__astropy-13977": "COMPATIBILITY_PROBLEMS",
    "astropy__astropy-14096": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "astropy__astropy-7336": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "django__django-11885": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "django__django-13023": "COMPATIBILITY_PROBLEMS",
    "django__django-14672": "DATA_FIDELITY_PROBLEMS",
    "django__django-15277": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "django__django-15731": "DATA_FIDELITY_PROBLEMS",
    "matplotlib__matplotlib-20859": "COMPATIBILITY_PROBLEMS",
    "pydata__xarray-4695": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "pytest-dev__pytest-10356": "DATA_FIDELITY_PROBLEMS",
    "pytest-dev__pytest-5809": "COMPATIBILITY_PROBLEMS",
    "scikit-learn__scikit-learn-14496": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-12419": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-12489": "COMPATIBILITY_PROBLEMS",
    "sympy__sympy-13091": "COMPATIBILITY_PROBLEMS",
    "sympy__sympy-13615": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-13974": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-17139": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-20438": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-21596": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-21847": "DATA_FIDELITY_PROBLEMS",
    "sympy__sympy-22914": "RENDERING_AND_VISUAL_PROBLEMS",
    "sympy__sympy-24539": "DATA_FIDELITY_PROBLEMS",
}


def final_codebook(config: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    output = Path(config["output_dir"])
    source = output / "solver_demand_codebook_frozen.csv"
    rows = [row for row in read_csv(source) if row["code"] in FINAL_CODES]
    if {row["code"] for row in rows} != FINAL_CODES:
        raise PipelineError("the v0 codebook does not contain all five final classes")
    for row in rows:
        row["taxonomy_version"] = VERSION
        row["exclusion_criteria"] = []
        row["neighbor_distinctions"] = [
            *row["neighbor_distinctions"],
            "In the final exhaustive taxonomy, choose this class when it is the closest fit by dominant solver work.",
        ]
    destination = output / "solver_demand_codebook_final_v1.csv"
    write_csv(destination, rows, CODEBOOK_FIELDS)
    payload = codebook_rows_to_payload(rows)
    validate_codebook_payload(payload, config)
    return payload, destination


def main() -> None:
    config = load_config("config.yaml")
    output = Path(config["output_dir"])
    codebook, codebook_path = final_codebook(config)
    current_path = output / "case_classifications.csv"
    backup_path = output / "case_classifications_v0.csv"
    if not backup_path.exists():
        shutil.copy2(current_path, backup_path)
    previous = read_csv(backup_path)
    retained = {row["case_id"]: row for row in previous if row.get("primary_solver_demand_class") in FINAL_CODES}
    target_rows = {row["case_id"]: row for row in previous if row["case_id"] not in retained}
    if set(target_rows) != set(FINAL_REMAP):
        missing = sorted(set(target_rows) - set(FINAL_REMAP))
        extra = sorted(set(FINAL_REMAP) - set(target_rows))
        raise PipelineError(f"remap does not match v0 targets; missing={missing}, extra={extra}")
    remap_audit = []
    revised = {}
    for case_id, row in target_rows.items():
        old_class = row.get("primary_solver_demand_class") or row.get("classification_status")
        new_class = FINAL_REMAP[case_id]
        updated = dict(row)
        updated["primary_solver_demand_class"] = new_class
        updated["classification_status"] = "CLASSIFIED"
        updated["uncertainty_reason"] = ""
        updated["pipeline_error"] = ""
        updated["taxonomy_version"] = VERSION
        revised[case_id] = updated
        remap_audit.append(
            {
                "case_id": case_id,
                "v0_class_or_status": old_class,
                "final_primary_class": new_class,
                "task_summary": row.get("task_summary", ""),
            }
        )
    write_csv(
        output / "final_five_remapping.csv",
        remap_audit,
        ["case_id", "v0_class_or_status", "final_primary_class", "task_summary"],
    )

    combined = retained | revised
    ordered = [combined[row["case_id"]] for row in read_csv(output / "enriched_cases.csv")]
    for row in ordered:
        row["taxonomy_version"] = VERSION
    write_csv(current_path, ordered, CLASSIFICATION_FIELDS)

    counts = Counter(row["primary_solver_demand_class"] for row in ordered)
    distribution = output / "taxonomy_distribution_final_v1.csv"
    write_csv(
        distribution,
        [{"taxonomy_version": VERSION, "class": code, "case_count": counts[code]} for code in sorted(FINAL_CODES)],
        ["taxonomy_version", "class", "case_count"],
    )
    report = output / "final_five_reclassification_report.md"
    report.write_text(
        "# Final Five-Class Taxonomy\n\n"
        + f"Retained from v0: {len(retained)}\n\nDeterministically remapped: {len(revised)}\n\n"
        + "## Final distribution\n\n"
        + "\n".join(f"- {code}: {counts[code]}" for code in sorted(FINAL_CODES))
        + "\n",
        encoding="utf-8",
    )
    print(f"Final taxonomy: {codebook_path}")
    print(f"Retained: {len(retained)}; deterministically remapped: {len(revised)}")
    for code, count in counts.most_common():
        print(f"  {code}: {count}")


if __name__ == "__main__":
    main()
