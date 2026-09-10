#!/usr/bin/env python3
"""Classify the 350 missing SWE-bench cases with concurrent direct API requests."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "outputs" / "solver_demand_v1"
ENRICHED = OUTPUT / "enriched_cases.csv"
CLASSIFICATIONS = OUTPUT / "case_classifications.csv"
PROMPT = HERE / "prompts" / "classify_final_five.md"
SCHEMA = HERE / "schemas" / "final_five_classification.json"
RUN_DIR = OUTPUT / "final_five_direct"
CACHE_DIR = RUN_DIR / "responses"
AUDIT_350 = RUN_DIR / "classifications_350.csv"
DISTRIBUTION = RUN_DIR / "taxonomy_distribution_500.csv"
BACKUP_150 = OUTPUT / "case_classifications.pre_final_five_direct.csv"

FINAL_CODES = {
    "DATA_FIDELITY_PROBLEMS",
    "TRACING_AND_OBSERVABILITY_PROBLEMS",
    "RENDERING_AND_VISUAL_PROBLEMS",
    "COMPATIBILITY_PROBLEMS",
    "PARSING_PROBLEMS",
}
OUTPUT_KEYS = ["quote", "reason", "classification"]
CSV_FIELDS = [
    "case_id",
    "task_summary",
    "overall_demand",
    "solver_profile",
    "primary_solver_demand_class",
    "interpretation_demand",
    "diagnosis_demand",
    "implementation_demand",
    "verification_demand",
    "dominant_demand",
    "repository_knowledge_needed",
    "diagnostic_work_required",
    "implementation_work_required",
    "verification_work_required",
    "root_cause_summary",
    "solution_summary",
    "evidence",
    "evidence_warnings",
    "confidence",
    "classification_status",
    "uncertainty_reason",
    "taxonomy_version",
    "pipeline_error",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(sys.maxsize)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    temporary.replace(path)


def evidence_input(case: dict[str, str]) -> str:
    sections = [
        ("CASE ID", case["case_id"]),
        ("REPOSITORY", case.get("repo", "")),
        ("PROBLEM STATEMENT", case.get("problem_statement", "")),
        ("HINTS", case.get("hints_text", "")),
        ("GOLD PATCH", case.get("patch", "")),
        ("GOLD TEST PATCH", case.get("test_patch", "")),
        ("FAIL TO PASS TESTS", case.get("FAIL_TO_PASS", "")),
        ("PASS TO PASS TESTS", case.get("PASS_TO_PASS", "")),
    ]
    candidates = quote_candidates(case)
    sections.append(
        (
            "VERBATIM QUOTE CANDIDATES",
            "\n".join(f"- {candidate}" for candidate in candidates),
        )
    )
    return "\n\n".join(f"=== {name} ===\n{value}" for name, value in sections if value)


def quote_candidates(case: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    for field in ("problem_statement", "hints_text"):
        text = case.get(field, "")
        for paragraph in re.split(r"\n\s*\n", text):
            paragraph = " ".join(paragraph.split()).strip()
            if 20 <= len(paragraph) <= 500:
                candidates.append(paragraph)
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
                sentence = sentence.strip()
                if 20 <= len(sentence) <= 280:
                    candidates.append(sentence)
    if not candidates:
        for field in ("patch", "test_patch"):
            for line in case.get(field, "").splitlines():
                line = line.strip()
                if 20 <= len(line) <= 280 and not line.startswith(("diff --git", "index ", "@@")):
                    candidates.append(line)
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen and quote_source(candidate, case) is not None:
            unique.append(candidate)
            seen.add(candidate)
    return unique[:20]


def normalized(value: str) -> str:
    return " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split())


def quote_source(quote: str, case: dict[str, str]) -> str | None:
    for source, field in (
        ("problem_statement", "problem_statement"),
        ("hints_text", "hints_text"),
        ("patch", "patch"),
        ("test_patch", "test_patch"),
        ("FAIL_TO_PASS", "FAIL_TO_PASS"),
        ("PASS_TO_PASS", "PASS_TO_PASS"),
    ):
        if normalized(quote) and normalized(quote) in normalized(case.get(field, "")):
            return source
    return None


def validate_result(result: Any, case: dict[str, str]) -> str:
    if not isinstance(result, dict) or list(result) != OUTPUT_KEYS:
        raise ValueError(f"expected ordered keys {OUTPUT_KEYS}; received {list(result) if isinstance(result, dict) else type(result)}")
    if not all(isinstance(result[key], str) and result[key].strip() for key in OUTPUT_KEYS):
        raise ValueError("all three result fields must be non-empty strings")
    if result["classification"] not in FINAL_CODES:
        raise ValueError(f"invalid classification: {result['classification']}")
    source = quote_source(result["quote"], case)
    if source is None:
        raise ValueError("quote was not copied verbatim from the supplied evidence")
    return source


def response_request(
    client: Any,
    case: dict[str, str],
    prompt: str,
    schema: dict[str, Any],
    model: str,
    reasoning_effort: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    request: dict[str, Any] = {
        "model": model,
        "instructions": prompt,
        "input": evidence_input(case),
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "final_five_classification",
                "schema": schema,
                "strict": True,
            }
        },
    }
    if reasoning_effort:
        request["reasoning"] = {"effort": reasoning_effort}
    response = client.responses.create(**request)
    result = json.loads(response.output_text)
    source = quote_source(result.get("quote", ""), case)
    usage = response.usage.model_dump(mode="json") if response.usage else {}
    return result, {"response_id": response.id, "usage": usage, "quote_source": source}


def classify_one(
    client: Any,
    case: dict[str, str],
    prompt: str,
    schema: dict[str, Any],
    model: str,
    reasoning_effort: str,
    max_retries: int,
) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{case['case_id']}.json"
    if cache.exists():
        record = json.loads(cache.read_text(encoding="utf-8"))
        validate_result(record["result"], case)
        return record

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            result, metadata = response_request(
                client, case, prompt, schema, model, reasoning_effort
            )
            source = validate_result(result, case)
            record = {
                "case_id": case["case_id"],
                "result": result,
                "quote_source": source,
                "model": model,
                **metadata,
            }
            temporary = cache.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(cache)
            return record
        except Exception as error:  # noqa: BLE001 - retry API and validation failures per case.
            last_error = error
            if attempt < max_retries:
                time.sleep(min(20, 2**attempt) + random.random())
    raise RuntimeError(f"{case['case_id']}: {last_error}") from last_error


def merge_results(cases: list[dict[str, str]], records: list[dict[str, Any]]) -> None:
    baseline = BACKUP_150 if BACKUP_150.exists() else CLASSIFICATIONS
    previous = read_csv(baseline)
    retained = {row["case_id"]: row for row in previous}
    if len(retained) != 150:
        raise RuntimeError(f"expected 150 retained classifications; found {len(retained)}")
    if not BACKUP_150.exists():
        shutil.copy2(CLASSIFICATIONS, BACKUP_150)

    new_by_id = {record["case_id"]: record for record in records}
    missing_ids = {case["case_id"] for case in cases} - retained.keys()
    if set(new_by_id) != missing_ids or len(new_by_id) != 350:
        raise RuntimeError(
            f"refusing incomplete merge: new={len(new_by_id)} expected={len(missing_ids)}"
        )

    audit: list[dict[str, str]] = []
    combined: dict[str, dict[str, str]] = dict(retained)
    for case_id, record in new_by_id.items():
        result = record["result"]
        source = record["quote_source"]
        audit.append({"case_id": case_id, **result, "quote_source": source})
        combined[case_id] = {
            "case_id": case_id,
            "task_summary": result["reason"],
            "primary_solver_demand_class": result["classification"],
            "root_cause_summary": result["reason"],
            "evidence": json.dumps(
                [{"source": source, "quote": result["quote"]}], ensure_ascii=False
            ),
            "classification_status": "CLASSIFIED",
            "taxonomy_version": "v1-final-five-direct",
        }

    ordered = [combined[case["case_id"]] for case in cases]
    if len(ordered) != 500 or len({row["case_id"] for row in ordered}) != 500:
        raise RuntimeError("final classification output is not exactly 500 unique cases")
    invalid = [
        row["case_id"]
        for row in ordered
        if row.get("primary_solver_demand_class") not in FINAL_CODES
    ]
    if invalid:
        raise RuntimeError(f"final output contains invalid classifications: {invalid[:10]}")

    write_csv(AUDIT_350, sorted(audit, key=lambda row: row["case_id"]), ["case_id", *OUTPUT_KEYS, "quote_source"])
    write_csv(CLASSIFICATIONS, ordered, CSV_FIELDS)
    counts = Counter(row["primary_solver_demand_class"] for row in ordered)
    write_csv(
        DISTRIBUTION,
        [{"classification": code, "case_count": counts[code]} for code in sorted(FINAL_CODES)],
        ["classification", "case_count"],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--concurrency", type=int, default=15)
    parser.add_argument("--max-retries", type=int, default=4)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")
    from openai import OpenAI

    cases = read_csv(ENRICHED)
    baseline = BACKUP_150 if BACKUP_150.exists() else CLASSIFICATIONS
    existing = read_csv(baseline)
    existing_ids = {row["case_id"] for row in existing}
    pending = [case for case in cases if case["case_id"] not in existing_ids]
    if len(cases) != 500 or len(existing_ids) != 150 or len(pending) != 350:
        raise SystemExit(
            f"expected 500 cases, 150 retained, and 350 pending; found {len(cases)}, "
            f"{len(existing_ids)}, and {len(pending)}"
        )

    prompt = PROMPT.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if list(schema["properties"]) != OUTPUT_KEYS:
        raise SystemExit(f"schema keys must be ordered as {OUTPUT_KEYS}")
    if set(schema["properties"]["classification"]["enum"]) != FINAL_CODES:
        raise SystemExit("schema classification enum does not match FINAL_CODES")

    client = OpenAI()
    records: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                classify_one,
                client,
                case,
                prompt,
                schema,
                args.model,
                args.reasoning_effort,
                args.max_retries,
            ): case["case_id"]
            for case in pending
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            case_id = futures[future]
            try:
                records[case_id] = future.result()
            except Exception as error:  # noqa: BLE001 - report every failed case before exiting.
                errors.append(str(error))
            with lock:
                print(
                    f"\rClassified {completed}/350 (valid={len(records)}, errors={len(errors)})",
                    end="",
                    flush=True,
                )
    print()
    if errors:
        (RUN_DIR / "errors.txt").write_text("\n".join(errors) + "\n", encoding="utf-8")
        raise SystemExit(f"classification incomplete: {len(errors)} errors; rerun to resume")

    merge_results(cases, list(records.values()))
    (RUN_DIR / "errors.txt").unlink(missing_ok=True)
    print(f"Merged 500 classifications: {CLASSIFICATIONS}")
    print(f"New 350 audit: {AUDIT_350}")


if __name__ == "__main__":
    main()
