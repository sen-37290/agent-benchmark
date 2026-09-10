"""Submit and collect the primary-only SWE-bench taxonomy through OpenAI Batch."""

from __future__ import annotations

import argparse
import copy
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline import (
    CLASSIFICATION_FIELDS,
    ENRICHED_FIELDS,
    NORMALIZED_FIELDS,
    PipelineError,
    _string_cell,
    _case_input,
    codebook_rows_to_payload,
    freeze_codebook,
    generate_taxonomy_review,
    load_config,
    load_verified_records,
    load_prompt,
    load_schema,
    read_csv,
    validate_codebook_payload,
    validate_evidence,
    validate_schema,
    write_csv,
)


TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
FINAL_FIVE_VERSION = "v1-final-five"
FINAL_FIVE_CODES = [
    "DATA_FIDELITY_PROBLEMS",
    "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "RENDERING_AND_VISUAL_PROBLEMS",
    "COMPATIBILITY_PROBLEMS",
    "PARSING_PROBLEMS",
]
FINAL_FIVE_GUIDANCE = """

The five classes in this codebook are exhaustive for this run. You MUST choose the closest class
by dominant solver work, set classification_status to CLASSIFIED, and provide that class in
primary_solver_demand_class. Do not return NEW_CLASS or UNCERTAIN.
"""


def paths(config: dict[str, Any], version: str) -> dict[str, Path]:
    output = Path(config["output_dir"])
    return {
        "input": output / f"batch_input_{version}.jsonl",
        "job": output / f"batch_job_{version}.json",
        "output": output / f"batch_output_{version}.jsonl",
        "errors": output / f"batch_errors_{version}.jsonl",
        "distribution": output / f"taxonomy_distribution_{version}.csv",
    }


def client() -> Any:
    if not os.environ.get("OPENAI_API_KEY"):
        raise PipelineError("OPENAI_API_KEY is not set")
    from openai import OpenAI

    return OpenAI()


def freeze(config: dict[str, Any], version: str) -> Path:
    if version == FINAL_FIVE_VERSION:
        final = Path(config["output_dir"]) / "solver_demand_codebook_final_v1.csv"
        if not final.exists():
            raise PipelineError(f"final five-class codebook does not exist: {final}")
        return final
    frozen = Path(config["output_dir"]) / "solver_demand_codebook_frozen.csv"
    return frozen if frozen.exists() else freeze_codebook(config, approve=True, version=version)


def load_frozen(config: dict[str, Any], version: str) -> dict[str, Any]:
    frozen = freeze(config, version)
    rows = read_csv(frozen)
    versions = {row["taxonomy_version"] for row in rows}
    if versions != {version}:
        raise PipelineError(f"expected frozen taxonomy {version}; found {sorted(versions)}")
    codebook = codebook_rows_to_payload(rows)
    validate_codebook_payload(codebook, config)
    return codebook


def ensure_full_enriched(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain the scraped 150 and append official evidence for the other 350."""
    target = Path(config["output_dir"]) / "enriched_cases.csv"
    existing = read_csv(target)
    by_id = {row["case_id"]: row for row in existing}
    verified = load_verified_records(config)
    verified_ids = {str(row["instance_id"]) for row in verified}
    if len(verified) != 500 or len(verified_ids) != 500:
        raise PipelineError(f"expected 500 unique SWE-bench Verified records; found {len(verified_ids)}")
    for source in verified:
        case_id = str(source["instance_id"])
        if case_id in by_id:
            continue
        problem = _string_cell(source.get("problem_statement"))
        patch = _string_cell(source.get("patch"))
        record = {field: "" for field in NORMALIZED_FIELDS}
        record.update(
            {
                "case_id": case_id,
                "source_id_column": "SWE-bench_Verified.instance_id",
                "raw_issue": problem,
                "raw_pr": patch,
                "normalized_issue": problem,
                "normalized_pr": patch,
                "validation_warnings": ["OFFICIAL_DATASET_EVIDENCE_ONLY"],
            }
        )
        for field in ENRICHED_FIELDS[len(NORMALIZED_FIELDS) :]:
            source_field = "difficulty" if field == "dataset_difficulty_reference" else field
            value = source.get(source_field)
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            record[field] = _string_cell(value)
        by_id[case_id] = record
    ordered = [by_id[str(row["instance_id"])] for row in verified]
    if len(ordered) != 500:
        raise PipelineError(f"expected 500 enriched cases; found {len(ordered)}")
    write_csv(target, ordered, ENRICHED_FIELDS)
    return ordered


def prepare(config: dict[str, Any], version: str) -> Path:
    codebook = load_frozen(config, version)
    schema = load_schema("classification.json")
    instructions = load_prompt("classify_case.md")
    if version == FINAL_FIVE_VERSION:
        instructions += FINAL_FIVE_GUIDANCE
        schema = copy.deepcopy(schema)
        schema["properties"]["primary_solver_demand_class"] = {
            "type": "string",
            "enum": FINAL_FIVE_CODES,
        }
        schema["properties"]["classification_status"] = {
            "type": "string",
            "enum": ["CLASSIFIED"],
        }
        schema["properties"]["uncertainty_reason"] = {"type": "null"}
    model = config["model"]["name"]
    effort = config["model"].get("reasoning_effort")
    records = []
    cases = ensure_full_enriched(config)
    existing_path = Path(config["output_dir"]) / "case_classifications.csv"
    completed = {row["case_id"] for row in read_csv(existing_path)} if existing_path.exists() else set()
    pending = [case for case in cases if case["case_id"] not in completed]
    for case in pending:
        body: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": _case_input(case, codebook),
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "solver_demand_classification",
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        if effort:
            body["reasoning"] = {"effort": effort}
        records.append(
            {"custom_id": case["case_id"], "method": "POST", "url": "/v1/responses", "body": body}
        )
    target = paths(config, version)["input"]
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    if len(records) != 350:
        raise PipelineError(f"expected exactly 350 missing classifications; found {len(records)}")
    print(f"Prepared {len(records)} missing requests: {target}")
    return target


def submit(config: dict[str, Any], version: str) -> dict[str, Any]:
    target = prepare(config, version)
    api = client()
    with target.open("rb") as source:
        uploaded = api.files.create(file=source, purpose="batch")
    batch = api.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/responses",
        completion_window="24h",
        metadata={"project": "swebench-primary-taxonomy", "taxonomy_version": version},
    )
    state = batch.model_dump(mode="json")
    state["submitted_at"] = datetime.now(UTC).isoformat()
    paths(config, version)["job"].write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Submitted {batch.id}: {batch.status}")
    return state


def read_job(config: dict[str, Any], version: str) -> dict[str, Any]:
    job = paths(config, version)["job"]
    if not job.exists():
        raise PipelineError(f"batch job file does not exist: {job}")
    return json.loads(job.read_text(encoding="utf-8"))


def status(config: dict[str, Any], version: str) -> dict[str, Any]:
    api = client()
    previous = read_job(config, version)
    batch = api.batches.retrieve(previous["id"])
    state = batch.model_dump(mode="json")
    paths(config, version)["job"].write_text(json.dumps(state, indent=2), encoding="utf-8")
    counts = state.get("request_counts") or {}
    print(f"{state['id']}: {state['status']} ({counts})")
    return state


def response_text(body: dict[str, Any]) -> str:
    for item in body.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content["text"]
    raise PipelineError("successful response contained no output_text")


def collect(config: dict[str, Any], version: str) -> Path:
    batch_paths = paths(config, version)
    state = read_job(config, version)
    api = None
    if state.get("status") != "completed" or not batch_paths["output"].exists():
        api = client()
        state = status(config, version)
    if state["status"] != "completed":
        if state["status"] in TERMINAL_STATUSES:
            raise PipelineError(f"batch ended with status {state['status']}")
        raise PipelineError(f"batch is not ready: {state['status']}")

    if batch_paths["output"].exists():
        output_text = batch_paths["output"].read_text(encoding="utf-8")
    else:
        assert api is not None
        output_text = api.files.content(state["output_file_id"]).text
        batch_paths["output"].write_text(output_text, encoding="utf-8")
        if state.get("error_file_id"):
            batch_paths["errors"].write_text(api.files.content(state["error_file_id"]).text, encoding="utf-8")

    cases = {row["case_id"]: row for row in ensure_full_enriched(config)}
    destination = Path(config["output_dir"]) / "case_classifications.csv"
    retained_rows = read_csv(destination) if destination.exists() else []
    retained = {row["case_id"]: row for row in retained_rows}
    schema = load_schema("classification.json")
    codebook = load_frozen(config, version)
    allowed = {row["code"] for row in codebook["primary_classes"]}
    results: dict[str, dict[str, Any]] = {}
    for line in output_text.splitlines():
        envelope = json.loads(line)
        case_id = envelope["custom_id"]
        response = envelope.get("response")
        if not response or response.get("status_code") != 200:
            continue
        result = json.loads(response_text(response["body"]))
        validate_schema(result, schema)
        primary = result["primary_solver_demand_class"]
        # Some models repeat the status sentinel in the nullable class field.
        if result["classification_status"] != "CLASSIFIED" and primary in {
            "NEW_CLASS",
            "UNCERTAIN",
            "",
        }:
            result["primary_solver_demand_class"] = None
            primary = None
        if result["classification_status"] == "CLASSIFIED" and primary not in allowed:
            raise PipelineError(f"{case_id} used unknown primary class {primary}")
        if result["classification_status"] != "CLASSIFIED" and primary is not None:
            raise PipelineError(f"{case_id} selected a class with status {result['classification_status']}")
        if case_id in retained:
            raise PipelineError(f"Batch unexpectedly reclassified retained case {case_id}")
        if version == FINAL_FIVE_VERSION and result["classification_status"] != "CLASSIFIED":
            raise PipelineError(f"{case_id} was not classified into the exhaustive final five")
        results[case_id] = {
            "case_id": case_id,
            **result,
            "evidence_warnings": validate_evidence(result, cases[case_id]),
            "taxonomy_version": version,
            "pipeline_error": "",
        }

    expected_new = cases.keys() - retained.keys()
    missing = expected_new - results.keys()
    unexpected = results.keys() - expected_new
    if missing or unexpected or len(results) != 350:
        raise PipelineError(
            f"incomplete Batch results: received={len(results)} missing={sorted(missing)[:10]} "
            f"unexpected={sorted(unexpected)[:10]}"
        )
    combined = retained | results
    if len(combined) != 500 or set(combined) != set(cases):
        raise PipelineError(f"refusing incomplete classification merge: {len(combined)} rows")
    ordered = [combined[case_id] for case_id in cases]
    write_csv(destination, ordered, CLASSIFICATION_FIELDS)

    counts = Counter(
        row.get("primary_solver_demand_class") or row.get("classification_status", "UNKNOWN")
        for row in ordered
    )
    write_csv(
        batch_paths["distribution"],
        [{"taxonomy_version": version, "class": name, "case_count": count} for name, count in counts.most_common()],
        ["taxonomy_version", "class", "case_count"],
    )
    print(f"Collected {len(ordered)} cases: {destination}")
    for name, count in counts.most_common():
        print(f"  {name}: {count}")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "submit", "status", "collect"))
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--version", default="v0")
    args = parser.parse_args()
    config = load_config(args.config)
    globals()[args.action](config, args.version)


if __name__ == "__main__":
    main()
