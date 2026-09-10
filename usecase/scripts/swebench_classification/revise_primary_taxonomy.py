"""Apply the human-reviewed v0 primary-class names to the draft codebook."""

from __future__ import annotations

import csv
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
DRAFT = HERE / "outputs" / "solver_demand_v1" / "solver_demand_codebook_draft.csv"

RENAMES = {
    "CONTRACT_COMPAT": "COMPATIBILITY_PROBLEMS",
    "EXECUTION_FLOW": "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "REPRESENTATION_INVARIANTS": "DATA_FIDELITY_PROBLEMS",
    "EXTENSIBILITY_INTEROP": "EXTENSIBILITY_PROBLEMS",
    "PARSING_SEMANTICS": "PARSING_PROBLEMS",
    "RENDERED_OUTPUT": "RENDERING_AND_VISUAL_PROBLEMS",
    "RELATIONAL_REASONING": "RELATIONAL_REASONING_PROBLEMS",
    "VERIFICATION_MATRIX": "TEST_DESIGN_PROBLEMS",
}


def main() -> None:
    with DRAFT.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = list(reader.fieldnames or [])
        rows = [row for row in reader if row["record_type"] == "PRIMARY_CLASS"]

    for row in rows:
        row["code"] = RENAMES[row["code"]]
        row["required_capabilities"] = json.dumps([])
        for field in ("definition", "solver_actions", "inclusion_criteria", "exclusion_criteria", "neighbor_distinctions"):
            for old, new in RENAMES.items():
                row[field] = row[field].replace(old, new)

    with DRAFT.open("w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
