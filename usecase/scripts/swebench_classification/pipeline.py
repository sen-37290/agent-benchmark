from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml
from jsonschema import Draft202012Validator
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent
csv.field_size_limit(20_000_000)
JSON_COLUMNS = {
    "validation_warnings",
    "removed_issue_chrome",
    "removed_pr_chrome",
    "problem_type_candidates",
    "required_model_capabilities",
    "evidence",
    "evidence_warnings",
    "problem_codes",
    "repository_knowledge_needed",
    "diagnostic_work_required",
    "implementation_work_required",
    "verification_work_required",
    "provisional_capability_phrases",
    "secondary_capability_codes",
    "solver_actions",
    "required_capabilities",
    "required_capability_phrases",
    "neighbor_distinctions",
    "inclusion_criteria",
    "exclusion_criteria",
    "motivating_case_ids",
    "review_reasons",
}


class PipelineError(RuntimeError):
    pass


class StructuredLLM(Protocol):
    def complete(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        instructions: str,
        input_text: str,
        cache_scope: str,
    ) -> dict[str, Any]: ...


def load_config(path: Path | str = ROOT / "config.yaml") -> dict[str, Any]:
    config_path = Path(path).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["_config_path"] = str(config_path)
    config["_root"] = str(config_path.parent)
    output = Path(config["output_dir"])
    if not output.is_absolute():
        output = config_path.parent / output
    config["output_dir"] = str(output.resolve())
    return config


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _string_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _strip_obvious_leading_github_navigation(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Remove only a bounded, unmistakable GitHub repository-navigation prefix."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    marker = next((i for i, line in enumerate(lines[:20]) if line.strip() == "Repository navigation"), None)
    if marker is None:
        return normalized, []
    end = next(
        (i for i in range(marker + 1, min(len(lines), marker + 35)) if lines[i].strip() == "Insights"),
        None,
    )
    if end is None:
        return normalized, []
    removed = "".join(lines[: end + 1])
    cleaned = "".join(lines[end + 1 :]).lstrip("\n")
    return cleaned, [{"start": 0, "end": len(removed), "text": removed}]


def normalize_scrape(text: str, remove_navigation: bool = True) -> tuple[str, list[dict[str, Any]]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not remove_navigation:
        return normalized, []
    return _strip_obvious_leading_github_navigation(normalized)


NORMALIZED_FIELDS = [
    "case_id",
    "source_id_column",
    "raw_issue",
    "raw_pr",
    "normalized_issue",
    "normalized_pr",
    "raw_issue_sha256",
    "raw_pr_sha256",
    "removed_issue_chrome",
    "removed_pr_chrome",
    "validation_warnings",
]


def write_csv(path: Path, records: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writeheader()
        for record in records:
            row: dict[str, Any] = {}
            for field in fields:
                value = record.get(field)
                if isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                elif value is None:
                    value = ""
                row[field] = value
            writer.writerow(row)
    temporary.replace(path)


def read_csv(path: Path, json_columns: set[str] | None = None) -> list[dict[str, Any]]:
    json_columns = JSON_COLUMNS if json_columns is None else json_columns
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for column in json_columns & row.keys():
            if row[column]:
                row[column] = json.loads(row[column])
            else:
                row[column] = []
    return rows


def validate_and_convert_xlsx(config: dict[str, Any]) -> Path:
    source = Path(config["input_xlsx"]).expanduser().resolve()
    if not source.exists():
        raise PipelineError(f"input XLSX does not exist: {source}")

    workbook = load_workbook(source, read_only=True, data_only=False)
    if len(workbook.sheetnames) != 1:
        raise PipelineError(f"expected one worksheet, found {workbook.sheetnames}")
    sheet = workbook[workbook.sheetnames[0]]
    rows = sheet.iter_rows()
    try:
        header_cells = next(rows)
    except StopIteration as error:
        raise PipelineError("input workbook is empty") from error

    for cell in header_cells:
        if cell.data_type == "f":
            raise PipelineError(f"formula found in header cell {cell.coordinate}")
    original_headers = [_string_cell(cell.value).strip() for cell in header_cells]
    aliases = config.get("normalization", {}).get("header_aliases", {})
    headers = [aliases.get(header, header) for header in original_headers]
    required = {"case_id", "issue", "pr"}
    missing = required - set(headers)
    if missing:
        raise PipelineError(f"missing required columns: {sorted(missing)}; found {headers}")
    if len(headers) != len(set(headers)):
        raise PipelineError(f"duplicate columns after aliasing: {headers}")

    index = {name: headers.index(name) for name in required}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    remove_nav = config.get("normalization", {}).get("remove_obvious_github_navigation", True)

    for row_number, cells in enumerate(rows, start=2):
        if all(cell.value is None for cell in cells):
            continue
        for cell in cells:
            if cell.data_type == "f":
                raise PipelineError(f"formula found in {cell.coordinate}; raw text must be literal")
        values = [_string_cell(cell.value) for cell in cells]
        values.extend([""] * (len(headers) - len(values)))
        case_id = values[index["case_id"]].strip()
        issue = values[index["issue"]]
        pr = values[index["pr"]]
        warnings: list[str] = []
        if not case_id:
            raise PipelineError(f"missing case ID at worksheet row {row_number}")
        if case_id in seen:
            raise PipelineError(f"duplicate case ID {case_id!r} at worksheet row {row_number}")
        seen.add(case_id)
        if not issue:
            warnings.append("EMPTY_ISSUE")
        if not pr:
            warnings.append("EMPTY_PR")
        if original_headers[index["case_id"]] != "case_id":
            warnings.append(f"ALIASED_{original_headers[index['case_id']].upper()}_TO_CASE_ID")
        if issue.startswith(("=", "+", "-", "@")) or pr.startswith(("=", "+", "-", "@")):
            warnings.append("FORMULA_LIKE_TEXT_PREFIX_PRESERVED")
        normalized_issue, removed_issue = normalize_scrape(issue, remove_nav)
        normalized_pr, removed_pr = normalize_scrape(pr, remove_nav)
        records.append(
            {
                "case_id": case_id,
                "source_id_column": original_headers[index["case_id"]],
                "raw_issue": issue,
                "raw_pr": pr,
                "normalized_issue": normalized_issue,
                "normalized_pr": normalized_pr,
                "raw_issue_sha256": sha256_text(issue),
                "raw_pr_sha256": sha256_text(pr),
                "removed_issue_chrome": removed_issue,
                "removed_pr_chrome": removed_pr,
                "validation_warnings": warnings,
            }
        )
    workbook.close()

    if not records:
        raise PipelineError("input workbook contains no data rows")
    output = Path(config["output_dir"]) / "normalized_cases.csv"
    write_csv(output, records, NORMALIZED_FIELDS)

    roundtrip = read_csv(output)
    if len(roundtrip) != len(records):
        raise PipelineError("CSV round-trip changed the row count")
    for before, after in zip(records, roundtrip, strict=True):
        for field in ("case_id", "raw_issue", "raw_pr", "normalized_issue", "normalized_pr"):
            if before[field] != after[field]:
                raise PipelineError(f"CSV round-trip changed {field} for {before['case_id']}")
    return output


ENRICHED_FIELDS = NORMALIZED_FIELDS + [
    "repo",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "hints_text",
    "created_at",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
    "dataset_difficulty_reference",
]


def load_verified_records(config: dict[str, Any]) -> list[dict[str, Any]]:
    injected = config.get("_dataset_records")
    if injected is not None:
        return [dict(row) for row in injected]
    try:
        from datasets import Dataset, load_dataset
    except ImportError as error:
        raise PipelineError("SWE-bench enrichment requires the datasets package") from error
    cached = sorted(
        (Path.home() / ".cache" / "huggingface" / "datasets").glob(
            "princeton-nlp___swe-bench_verified/**/swe-bench_verified-test.arrow"
        )
    )
    if cached:
        dataset = Dataset.from_file(str(cached[-1]))
        return [dict(row) for row in dataset]
    try:
        dataset = load_dataset(config["dataset_id"], split=config.get("dataset_split", "test"))
    except Exception as load_error:
        raise PipelineError(f"could not load {config['dataset_id']}") from load_error
    return [dict(row) for row in dataset]


def enrich_with_verified_dataset(config: dict[str, Any], normalized_path: Path | None = None) -> Path:
    normalized_path = normalized_path or Path(config["output_dir"]) / "normalized_cases.csv"
    cases = read_csv(normalized_path)
    dataset_rows = load_verified_records(config)
    by_id: dict[str, dict[str, Any]] = {}
    for row in dataset_rows:
        instance_id = str(row.get("instance_id", "")).strip()
        if not instance_id:
            raise PipelineError("SWE-bench Verified record has no instance_id")
        if instance_id in by_id:
            raise PipelineError(f"duplicate SWE-bench Verified instance_id: {instance_id}")
        by_id[instance_id] = row
    missing = [case["case_id"] for case in cases if case["case_id"] not in by_id]
    if missing:
        raise PipelineError(f"{len(missing)} XLSX cases do not match SWE-bench Verified: {missing[:10]}")

    enriched: list[dict[str, Any]] = []
    for case in cases:
        source = by_id[case["case_id"]]
        record = dict(case)
        for field in (
            "repo",
            "base_commit",
            "patch",
            "test_patch",
            "problem_statement",
            "hints_text",
            "created_at",
            "version",
            "FAIL_TO_PASS",
            "PASS_TO_PASS",
            "environment_setup_commit",
        ):
            value = source.get(field)
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            record[field] = _string_cell(value)
        record["dataset_difficulty_reference"] = _string_cell(source.get("difficulty"))
        enriched.append(record)
    output = Path(config["output_dir"]) / "enriched_cases.csv"
    write_csv(output, enriched, ENRICHED_FIELDS)
    roundtrip = read_csv(output)
    if len(roundtrip) != len(cases):
        raise PipelineError("enriched CSV round-trip changed the row count")
    return output


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def load_prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")


def validate_schema(value: dict[str, Any], schema: dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(error.message for error in errors[:5])
        raise PipelineError(f"structured response failed schema validation: {detail}")


def _normalized_evidence_text(value: str) -> str:
    translations = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "…": "..."})
    return " ".join(value.translate(translations).split()).strip(" \"'")


def _evidence_quote_matches(quote: str, source: str) -> bool:
    if quote in source:
        return True
    normalized_quote = _normalized_evidence_text(quote)
    normalized_source = _normalized_evidence_text(source)
    if normalized_quote and normalized_quote in normalized_source:
        return True
    fragments = [part.strip(" \"'") for part in re.split(r"\.{3,}", normalized_quote)]
    significant = [part for part in fragments if len(part) >= 15]
    if len(significant) < 2:
        return False
    position = 0
    for fragment in significant:
        found = normalized_source.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


def validate_evidence(value: dict[str, Any], case: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for item in value.get("evidence", []):
        quote = item.get("quote", "")
        source = item.get("source")
        if not quote:
            warnings.append(f"EMPTY_{source}_EVIDENCE_QUOTE")
            continue
        source_fields = {
            "issue": "raw_issue",
            "problem_statement": "problem_statement",
            "hints_text": "hints_text",
            "pr": "raw_pr",
            "patch": "patch",
            "test_patch": "test_patch",
            "FAIL_TO_PASS": "FAIL_TO_PASS",
            "PASS_TO_PASS": "PASS_TO_PASS",
        }
        if source not in source_fields:
            raise PipelineError(f"unknown evidence source: {source}")
        raw = case[source_fields[source]]
        if not _evidence_quote_matches(quote, raw):
            warnings.append(f"UNMATCHED_{source}: {quote[:160]}")
    return warnings


class OpenAIResponsesLLM:
    def __init__(self, config: dict[str, Any], cache_dir: Path):
        try:
            from openai import OpenAI
        except ImportError as error:
            raise PipelineError("install requirements.txt before making live API calls") from error
        if not os.environ.get("OPENAI_API_KEY"):
            raise PipelineError("OPENAI_API_KEY is not set")
        self.client = OpenAI()
        self.model = config["model"]["name"]
        self.reasoning_effort = config["model"].get("reasoning_effort")
        self.max_retries = int(config["model"].get("max_retries", 3))
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def complete(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        instructions: str,
        input_text: str,
        cache_scope: str,
    ) -> dict[str, Any]:
        cache_key = sha256_text(
            json.dumps(
                {
                    "scope": cache_scope,
                    "model": self.model,
                    "reasoning": self.reasoning_effort,
                    "schema": schema,
                    "instructions": instructions,
                    "input": input_text,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))["result"]

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                request: dict[str, Any] = {
                    "model": self.model,
                    "instructions": instructions,
                    "input": input_text,
                    "store": False,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "schema": schema,
                            "strict": True,
                        }
                    },
                }
                if self.reasoning_effort:
                    request["reasoning"] = {"effort": self.reasoning_effort}
                response = self.client.responses.create(**request)
                result = json.loads(response.output_text)
                validate_schema(result, schema)
                payload = {
                    "created_at": datetime.now(UTC).isoformat(),
                    "response_id": response.id,
                    "model": self.model,
                    "result": result,
                }
                temporary = cache_path.with_suffix(".tmp")
                with self._lock:
                    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(cache_path)
                return result
            except Exception as error:  # noqa: BLE001 - SDK error types vary by version.
                last_error = error
                if attempt < self.max_retries:
                    time.sleep(min(8, 2 ** (attempt - 1)))
        raise PipelineError(f"LLM request failed after {self.max_retries} attempts: {last_error}")


READING_FIELDS = [
    "case_id",
    "task_summary",
    "expected_outcome",
    "solver_starting_information",
    "requirement_or_behavior_ambiguity",
    "repository_knowledge_needed",
    "diagnostic_work_required",
    "implementation_work_required",
    "verification_work_required",
    "root_cause_confirmed_by_patch",
    "solution_confirmed_by_patch",
    "interpretation_demand",
    "diagnosis_demand",
    "implementation_demand",
    "verification_demand",
    "dominant_demand",
    "provisional_capability_phrases",
    "evidence",
    "evidence_warnings",
    "confidence",
    "uncertainty",
]


def _case_input(case: dict[str, Any], codebook: dict[str, Any] | None = None) -> str:
    parts = [
        f"CASE ID: {case['case_id']}",
        f"REPOSITORY: {case['repo']}",
        "\n=== SOLVER-FACING EVIDENCE ===",
        "\nCLEANED ORIGINAL ISSUE:\n" + case["normalized_issue"],
        "\nSWE-BENCH PROBLEM_STATEMENT (REQUIRED READING):\n" + case["problem_statement"],
        "\nSWE-BENCH HINTS_TEXT:\n" + case["hints_text"],
        "\n=== AUDITOR-ONLY EVIDENCE: NOT AVAILABLE TO THE HYPOTHETICAL SOLVER ===",
        "\nCLEANED ORIGINAL PR:\n" + case["normalized_pr"],
        "\nGOLD PATCH:\n" + case["patch"],
        "\nGOLD TEST PATCH:\n" + case["test_patch"],
        "\nFAIL_TO_PASS TESTS:\n" + case["FAIL_TO_PASS"],
        "\nPASS_TO_PASS TESTS:\n" + case["PASS_TO_PASS"],
    ]
    if codebook is not None:
        parts.append("\nFROZEN SOLVER-DEMAND CODEBOOK:\n" + json.dumps(codebook, ensure_ascii=False, indent=2))
    return "\n".join(parts)


def _run_parallel(
    cases: list[dict[str, Any]], worker: Any, concurrency: int, label: str = "Processing cases"
) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    total = len(cases)
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {executor.submit(worker, case): case["case_id"] for case in cases}
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                results[case_id] = future.result()
            except Exception as error:  # noqa: BLE001 - preserve per-case failures in review output.
                results[case_id] = {
                    "case_id": case_id,
                    "classification_status": "UNCERTAIN",
                    "confidence": 0.0,
                    "uncertain_reason": str(error),
                    "pipeline_error": str(error),
                }
            completed += 1
            print(f"\r{label}: {completed}/{total}", end="", flush=True)
    if total:
        print()
    return [results[case["case_id"]] for case in cases]


def extract_case_readings(
    config: dict[str, Any], llm: StructuredLLM, normalized_path: Path | None = None
) -> Path:
    normalized_path = normalized_path or Path(config["output_dir"]) / "enriched_cases.csv"
    cases = read_csv(normalized_path)
    prompt = load_prompt("read_case.md")
    schema = load_schema("case_reading.json")

    def worker(case: dict[str, Any]) -> dict[str, Any]:
        result = llm.complete(
            schema_name="case_demand_analysis",
            schema=schema,
            instructions=prompt,
            input_text=_case_input(case),
            cache_scope="solver-demand-analysis-v1",
        )
        validate_schema(result, schema)
        evidence_warnings = validate_evidence(result, case)
        return {"case_id": case["case_id"], **result, "evidence_warnings": evidence_warnings}

    readings = _run_parallel(
        cases, worker, int(config["model"].get("concurrency", 3)), "Analyzing solver demand"
    )
    output = Path(config["output_dir"]) / "case_demand_analyses.csv"
    write_csv(output, readings, READING_FIELDS + ["classification_status", "pipeline_error"])
    return output


def select_diverse_sample(
    cases: list[dict[str, Any]], size: int, seed: int, exclude: set[str] | None = None
) -> list[str]:
    exclude = exclude or set()
    groups: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        if case["case_id"] in exclude:
            continue
        repository = case["case_id"].split("__", 1)[0]
        groups.setdefault(repository, []).append(case)
    rng = random.Random(seed)
    for group in groups.values():
        group.sort(key=lambda row: len(row.get("raw_issue", "")) + len(row.get("raw_pr", "")))
        buckets = [group[::2], group[1::2]]
        group[:] = []
        for bucket in buckets:
            rng.shuffle(bucket)
            group.extend(bucket)
    repositories = sorted(groups)
    selected: list[str] = []
    while repositories and len(selected) < size:
        for repository in list(repositories):
            if groups[repository]:
                selected.append(groups[repository].pop()["case_id"])
                if len(selected) == size:
                    break
            if not groups[repository]:
                repositories.remove(repository)
    return selected


CODEBOOK_FIELDS = [
    "taxonomy_version",
    "record_type",
    "code",
    "definition",
    "solver_actions",
    "required_capabilities",
    "inclusion_criteria",
    "exclusion_criteria",
    "neighbor_distinctions",
    "observable_solver_behavior",
    "motivating_case_ids",
]


def validate_codebook_payload(payload: dict[str, Any], config: dict[str, Any]) -> None:
    validate_schema(payload, load_schema("codebook.json"))
    primary = payload["primary_classes"]
    minimum = int(config.get("primary_class_min_count", 6))
    maximum = int(config.get("primary_class_max_count", 12))
    if not minimum <= len(primary) <= maximum:
        raise PipelineError(f"codebook must contain {minimum}-{maximum} primary classes")
    minimum_cases = int(config.get("primary_class_min_cases", 3))
    for entry in primary:
        if len(set(entry["motivating_case_ids"])) < minimum_cases:
            raise PipelineError(
                f"primary class {entry['code']} needs at least {minimum_cases} motivating cases"
            )
        if not entry["solver_actions"]:
            raise PipelineError(f"primary class {entry['code']} must describe solver actions")
    codes = [entry["code"] for entry in primary]
    if len(codes) != len(set(codes)):
        raise PipelineError("primary and capability codes must be globally unique")


def codebook_payload_to_rows(payload: dict[str, Any], version: str) -> list[dict[str, Any]]:
    rows = []
    for entry in payload["primary_classes"]:
        rows.append({"taxonomy_version": version, "record_type": "PRIMARY_CLASS", **entry})
    return rows


def codebook_rows_to_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    primary_fields = {
        "code",
        "definition",
        "solver_actions",
        "inclusion_criteria",
        "exclusion_criteria",
        "neighbor_distinctions",
        "motivating_case_ids",
    }
    return {
        "primary_classes": [
            {field: row.get(field, [] if field.endswith("s") else "") for field in primary_fields}
            for row in rows
            if row.get("record_type") == "PRIMARY_CLASS"
        ],
    }


def build_draft_codebook(config: dict[str, Any], llm: StructuredLLM) -> tuple[Path, list[str]]:
    output_dir = Path(config["output_dir"])
    cases = read_csv(output_dir / "enriched_cases.csv")
    readings = {row["case_id"]: row for row in read_csv(output_dir / "case_demand_analyses.csv")}
    sample_ids = select_diverse_sample(
        cases, int(config["open_coding_sample_size"]), int(config["random_seed"])
    )
    sample = [
        readings[case_id]
        for case_id in sample_ids
        if case_id in readings and not readings[case_id].get("pipeline_error")
    ]
    schema = load_schema("codebook.json")
    proposed = llm.complete(
        schema_name="solver_demand_codebook",
        schema=schema,
        instructions=load_prompt("build_taxonomy.md"),
        input_text=json.dumps(sample, ensure_ascii=False, indent=2),
        cache_scope="solver-demand-codebook-proposal-v1",
    )
    validate_schema(proposed, schema)
    result = llm.complete(
        schema_name="solver_demand_codebook",
        schema=schema,
        instructions=load_prompt("critic_codebook.md"),
        input_text=json.dumps({"sample": sample, "proposed_codebook": proposed}, ensure_ascii=False),
        cache_scope="solver-demand-codebook-critic-v1",
    )
    try:
        validate_codebook_payload(result, config)
    except PipelineError as initial_error:
        repair_error: PipelineError = initial_error
        for attempt in range(1, 3):
            repair_instructions = (
                load_prompt("critic_codebook.md")
                + "\n\nThe previous response failed validation. Correct the complete codebook. "
                + "Motivating case IDs must be distinct and every primary class must contain at "
                + f"least {config.get('primary_class_min_cases', 3)} distinct IDs.\n"
                + f"VALIDATION ERROR: {repair_error}"
            )
            result = llm.complete(
                schema_name="solver_demand_codebook",
                schema=schema,
                instructions=repair_instructions,
                input_text=json.dumps(
                    {"sample": sample, "invalid_codebook": result}, ensure_ascii=False
                ),
                cache_scope=f"solver-demand-codebook-repair-v1-attempt-{attempt}",
            )
            try:
                validate_codebook_payload(result, config)
                break
            except PipelineError as error:
                repair_error = error
        else:
            raise PipelineError(
                f"codebook still failed validation after two repair attempts: {repair_error}"
            ) from repair_error
    rows = codebook_payload_to_rows(result, "draft")
    output = output_dir / "solver_demand_codebook_draft.csv"
    write_csv(output, rows, CODEBOOK_FIELDS)
    (output_dir / "_open_sample_ids.json").write_text(
        json.dumps(sample_ids, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output, sample_ids


CLASSIFICATION_FIELDS = [
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


PROFILE_FIELDS = [
    "case_id",
    "overall_demand",
    "solver_profile",
    "interpretation_demand",
    "diagnosis_demand",
    "implementation_demand",
    "verification_demand",
    "dominant_demand",
    "task_summary",
    "solver_starting_information",
    "requirement_or_behavior_ambiguity",
    "repository_knowledge_needed",
    "diagnostic_work_required",
    "implementation_work_required",
    "verification_work_required",
    "required_capability_phrases",
    "root_cause_summary",
    "solution_summary",
    "evidence",
    "evidence_warnings",
    "confidence",
    "classification_status",
    "uncertainty_reason",
]


DEMAND_SCORE = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def derive_task_shape(analysis: dict[str, Any]) -> tuple[str, str]:
    axes = {
        "INTERPRETATION": analysis.get("interpretation_demand"),
        "DIAGNOSIS": analysis.get("diagnosis_demand"),
        "IMPLEMENTATION": analysis.get("implementation_demand"),
        "VERIFICATION": analysis.get("verification_demand"),
    }
    if any(value not in DEMAND_SCORE for value in axes.values()):
        return "UNCERTAIN", "UNCERTAIN"
    scores = {axis: DEMAND_SCORE[value] for axis, value in axes.items()}
    high_axes = [axis for axis, score in scores.items() if score == 3]
    if high_axes:
        overall = "HARD"
    elif all(score == 1 for score in scores.values()):
        overall = "EASY"
    else:
        overall = "MODERATE"

    if len(high_axes) >= 2:
        return overall, "BALANCED_MULTI_STAGE"
    if len(high_axes) == 1:
        return overall, {
            "INTERPRETATION": "UNDERSTANDING_LED",
            "DIAGNOSIS": "DIAGNOSIS_LED",
            "IMPLEMENTATION": "IMPLEMENTATION_LED",
            "VERIFICATION": "VERIFICATION_LED",
        }[high_axes[0]]
    if all(score == 1 for score in scores.values()):
        return overall, "STRAIGHTFORWARD"
    if scores["IMPLEMENTATION"] == 1 and max(
        scores["INTERPRETATION"], scores["DIAGNOSIS"]
    ) >= 2:
        return overall, "LOCAL_CHANGE_REQUIRING_REASONING"
    maximum = max(scores.values())
    leaders = [axis for axis, score in scores.items() if score == maximum]
    if len(leaders) == 1:
        return overall, {
            "INTERPRETATION": "UNDERSTANDING_LED",
            "DIAGNOSIS": "DIAGNOSIS_LED",
            "IMPLEMENTATION": "IMPLEMENTATION_LED",
            "VERIFICATION": "VERIFICATION_LED",
        }[leaders[0]]
    return overall, "BALANCED_MULTI_STAGE"


def build_task_profile_classifications(config: dict[str, Any]) -> Path:
    output_dir = Path(config["output_dir"])
    analyses = read_csv(output_dir / "case_demand_analyses.csv")
    records: list[dict[str, Any]] = []
    for analysis in analyses:
        overall, profile = derive_task_shape(analysis)
        uncertain = overall == "UNCERTAIN" or bool(analysis.get("pipeline_error"))
        records.append(
            {
                "case_id": analysis["case_id"],
                "overall_demand": overall,
                "solver_profile": profile,
                "interpretation_demand": analysis.get("interpretation_demand", ""),
                "diagnosis_demand": analysis.get("diagnosis_demand", ""),
                "implementation_demand": analysis.get("implementation_demand", ""),
                "verification_demand": analysis.get("verification_demand", ""),
                "dominant_demand": analysis.get("dominant_demand", ""),
                "task_summary": analysis.get("task_summary", ""),
                "solver_starting_information": analysis.get("solver_starting_information", ""),
                "requirement_or_behavior_ambiguity": analysis.get(
                    "requirement_or_behavior_ambiguity", ""
                ),
                "repository_knowledge_needed": analysis.get("repository_knowledge_needed", []),
                "diagnostic_work_required": analysis.get("diagnostic_work_required", []),
                "implementation_work_required": analysis.get("implementation_work_required", []),
                "verification_work_required": analysis.get("verification_work_required", []),
                "required_capability_phrases": analysis.get(
                    "provisional_capability_phrases", []
                ),
                "root_cause_summary": analysis.get("root_cause_confirmed_by_patch", ""),
                "solution_summary": analysis.get("solution_confirmed_by_patch", ""),
                "evidence": analysis.get("evidence", []),
                "evidence_warnings": analysis.get("evidence_warnings", []),
                "confidence": analysis.get("confidence", 0),
                "classification_status": "UNCERTAIN" if uncertain else "CLASSIFIED",
                "uncertainty_reason": analysis.get("pipeline_error", "")
                or analysis.get("uncertainty", ""),
            }
        )
    output = output_dir / "case_classifications.csv"
    write_csv(output, records, PROFILE_FIELDS)
    return output


def generate_task_profile_review(config: dict[str, Any]) -> Path:
    rows = read_csv(Path(config["output_dir"]) / "case_classifications.csv")
    profiles: dict[str, int] = {}
    demand: dict[str, int] = {}
    for row in rows:
        profiles[row["solver_profile"]] = profiles.get(row["solver_profile"], 0) + 1
        demand[row["overall_demand"]] = demand.get(row["overall_demand"], 0) + 1
    lines = [
        "# Task-Shape Classification Review",
        "",
        "These classes describe where the solving effort lies; they do not describe software domains or bug mechanisms.",
        "",
        "## Overall demand",
        "",
        *[f"- {name}: {count}" for name, count in sorted(demand.items())],
        "",
        "## Solver profiles",
        "",
        "- STRAIGHTFORWARD: the requested behavior, likely cause, and implementation are all locally understandable.",
        "- LOCAL_CHANGE_REQUIRING_REASONING: understanding or diagnosis requires meaningful reasoning, but implementation remains local.",
        "- UNDERSTANDING_LED: interpreting the intended behavior is the dominant challenge.",
        "- DIAGNOSIS_LED: locating and proving the root cause is the dominant challenge.",
        "- IMPLEMENTATION_LED: constructing the change is the dominant challenge.",
        "- VERIFICATION_LED: proving correctness is the dominant challenge.",
        "- BALANCED_MULTI_STAGE: substantial work is distributed across several stages.",
        "",
        "## Distribution",
        "",
        *[f"- {name}: {count}" for name, count in sorted(profiles.items())],
        "",
    ]
    output = Path(config["output_dir"]) / "task_profile_review.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def _classify_cases(
    config: dict[str, Any],
    llm: StructuredLLM,
    cases: list[dict[str, Any]],
    codebook: dict[str, Any],
    taxonomy_version: str,
    cache_scope: str,
) -> list[dict[str, Any]]:
    validate_codebook_payload(codebook, config)
    schema = load_schema("classification.json")
    prompt = load_prompt("classify_case.md")
    allowed_primary = {row["code"] for row in codebook["primary_classes"]}

    def worker(case: dict[str, Any]) -> dict[str, Any]:
        result = llm.complete(
            schema_name="solver_demand_classification",
            schema=schema,
            instructions=prompt,
            input_text=_case_input(case, codebook),
            cache_scope=cache_scope,
        )
        validate_schema(result, schema)
        evidence_warnings = validate_evidence(result, case)
        primary = result["primary_solver_demand_class"]
        if result["classification_status"] == "CLASSIFIED" and primary not in allowed_primary:
            raise PipelineError(f"CLASSIFIED result used unknown primary class: {primary}")
        if result["classification_status"] != "CLASSIFIED" and primary is not None:
            raise PipelineError("NEW_CLASS or UNCERTAIN result must not select a primary class")
        return {
            "case_id": case["case_id"],
            **result,
            "evidence_warnings": evidence_warnings,
            "taxonomy_version": taxonomy_version,
        }

    return _run_parallel(
        cases, worker, int(config["model"].get("concurrency", 3)), "Classifying cases"
    )


def validate_draft_codebook(
    config: dict[str, Any], llm: StructuredLLM, open_sample_ids: list[str] | None = None
) -> Path:
    output_dir = Path(config["output_dir"])
    cases = read_csv(output_dir / "enriched_cases.csv")
    if open_sample_ids is None:
        open_sample_ids = json.loads((output_dir / "_open_sample_ids.json").read_text(encoding="utf-8"))
    validation_ids = select_diverse_sample(
        cases,
        int(config["validation_sample_size"]),
        int(config["random_seed"]) + 1,
        exclude=set(open_sample_ids),
    )
    selected = [case for case in cases if case["case_id"] in set(validation_ids)]
    codebook = codebook_rows_to_payload(read_csv(output_dir / "solver_demand_codebook_draft.csv"))
    results = _classify_cases(
        config, llm, selected, codebook, "draft", "solver-demand-draft-validation-v1"
    )
    output = output_dir / "_taxonomy_validation.csv"
    write_csv(output, results, CLASSIFICATION_FIELDS)
    generate_taxonomy_review(config, codebook, results)
    return output


def generate_taxonomy_review(
    config: dict[str, Any], codebook: dict[str, Any], validation: list[dict[str, Any]]
) -> Path:
    lines = ["# Solver-Demand Taxonomy Review", "", "## Primary classes", ""]
    for entry in codebook["primary_classes"]:
        lines.extend(
            [
                f"### {entry['code']}",
                "",
                entry["definition"],
                "",
                "Solver actions:",
                *[f"- {value}" for value in entry["solver_actions"]],
                "",
                f"Motivating cases: {', '.join(entry['motivating_case_ids'])}",
                "",
            ]
        )
    status_counts: dict[str, int] = {}
    for row in validation:
        status = row.get("classification_status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    lines.extend(["", "## Unseen-case validation", ""])
    lines.extend(f"- {status}: {count}" for status, count in sorted(status_counts.items()))
    output = Path(config["output_dir"]) / "taxonomy_review.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def freeze_codebook(config: dict[str, Any], *, approve: bool, version: str) -> Path:
    if not approve:
        raise PipelineError(
            "set approve=True only after reviewing solver_demand_codebook_draft.csv and taxonomy_review.md"
        )
    if not version or version == "draft":
        raise PipelineError("provide a non-draft taxonomy version, for example v1")
    output_dir = Path(config["output_dir"])
    draft = output_dir / "solver_demand_codebook_draft.csv"
    rows = read_csv(draft)
    validate_codebook_payload(codebook_rows_to_payload(rows), config)
    frozen = output_dir / "solver_demand_codebook_frozen.csv"
    if frozen.exists():
        raise PipelineError(f"frozen codebook already exists and will not be overwritten: {frozen}")
    for row in rows:
        row["taxonomy_version"] = version
    write_csv(frozen, rows, CODEBOOK_FIELDS)
    approval = {
        "taxonomy_version": version,
        "approved_at": datetime.now(UTC).isoformat(),
        "draft_sha256": file_sha256(draft),
        "frozen_sha256": file_sha256(frozen),
    }
    (output_dir / "_freeze_approval.json").write_text(
        json.dumps(approval, indent=2), encoding="utf-8"
    )
    return frozen


def classify_all(config: dict[str, Any], llm: StructuredLLM) -> Path:
    output_dir = Path(config["output_dir"])
    cases = read_csv(output_dir / "enriched_cases.csv")
    rows = read_csv(output_dir / "solver_demand_codebook_frozen.csv")
    codebook = codebook_rows_to_payload(rows)
    versions = {row["taxonomy_version"] for row in rows}
    if len(versions) != 1:
        raise PipelineError(f"frozen codebook must have exactly one version; found {versions}")
    version = versions.pop()
    results = _classify_cases(
        config, llm, cases, codebook, version, f"final-reclassification-{version}"
    )
    output = output_dir / "case_classifications.csv"
    write_csv(output, results, CLASSIFICATION_FIELDS)
    return output


REVIEW_FIELDS = [
    "priority",
    "case_id",
    "review_reasons",
    "classification_status",
    "overall_demand",
    "solver_profile",
    "task_summary",
    "root_cause_summary",
    "confidence",
    "uncertainty_reason",
    "evidence_warnings",
]


def build_review_queue(config: dict[str, Any]) -> Path:
    output_dir = Path(config["output_dir"])
    rows = read_csv(output_dir / "case_classifications.csv")
    threshold = float(config.get("low_confidence_threshold", 0.70))
    queue: list[dict[str, Any]] = []
    for row in rows:
        reasons: list[str] = []
        priority = 0
        status = row.get("classification_status")
        if status == "NEW_CLASS":
            reasons.append("NEW_CLASS")
            priority += 100
        if status == "UNCERTAIN":
            reasons.append("UNCERTAIN")
            priority += 90
        confidence = float(row.get("confidence") or 0)
        if confidence < threshold:
            reasons.append("LOW_CONFIDENCE")
            priority += 50
        if row.get("pipeline_error"):
            reasons.append("PIPELINE_OR_EVIDENCE_ERROR")
            priority += 90
        if row.get("evidence_warnings"):
            reasons.append("UNMATCHED_EVIDENCE_QUOTE")
            priority += 30
        if reasons:
            queue.append(
                {
                    "priority": priority,
                    "case_id": row["case_id"],
                    "review_reasons": reasons,
                    "classification_status": status,
                    "overall_demand": row.get("overall_demand", ""),
                    "solver_profile": row.get("solver_profile", ""),
                    "task_summary": row.get("task_summary", ""),
                    "root_cause_summary": row.get("root_cause_summary", ""),
                    "confidence": confidence,
                    "uncertainty_reason": row.get("uncertainty_reason", ""),
                    "evidence_warnings": row.get("evidence_warnings", []),
                }
            )
    queue.sort(key=lambda row: (-row["priority"], row["case_id"]))
    output = output_dir / "review_queue.csv"
    write_csv(output, queue, REVIEW_FIELDS)
    return output


def generate_summary(config: dict[str, Any]) -> Path:
    output_dir = Path(config["output_dir"])
    rows = read_csv(output_dir / "case_classifications.csv")
    profile_counts: dict[str, int] = {}
    overall_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    demand_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        status = row.get("classification_status", "")
        status_counts[status] = status_counts.get(status, 0) + 1
        profile = row.get("solver_profile", "")
        overall = row.get("overall_demand", "")
        if profile:
            profile_counts[profile] = profile_counts.get(profile, 0) + 1
        if overall:
            overall_counts[overall] = overall_counts.get(overall, 0) + 1
        for axis in ("interpretation_demand", "diagnosis_demand", "implementation_demand", "verification_demand"):
            value = row.get(axis, "UNKNOWN")
            demand_counts.setdefault(axis, {})[value] = demand_counts.setdefault(axis, {}).get(value, 0) + 1
    lines = [
        "# SWE-bench Task-Shape Classification Summary",
        "",
        f"Cases classified: {len(rows)}",
        "",
        "## Classification status",
        "",
    ]
    lines.extend(f"- {status or 'UNKNOWN'}: {count}" for status, count in sorted(status_counts.items()))
    lines.extend(["", "## Overall demand", ""])
    lines.extend(f"- {code}: {count}" for code, count in sorted(overall_counts.items()))
    lines.extend(["", "## Solver profiles", ""])
    lines.extend(f"- {code}: {count}" for code, count in sorted(profile_counts.items(), key=lambda item: (-item[1], item[0])))
    lines.extend(["", "## Demand scorecard", ""])
    for axis, counts in demand_counts.items():
        lines.append(f"- {axis}: " + ", ".join(f"{value}={count}" for value, count in sorted(counts.items())))
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "The profiles are derived from the four existing case-level demand judgments. They describe where solving effort lies, not the software domain or bug mechanism. The issue and SWE-bench problem statement are solver-facing evidence; PR and patch content are auditor evidence.",
            "",
        ]
    )
    output = output_dir / "summary_report.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def write_manifest(config: dict[str, Any], outputs: list[Path]) -> Path:
    output_dir = Path(config["output_dir"])
    source = Path(config["input_xlsx"]).expanduser().resolve()
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "input_xlsx": str(source),
        "input_sha256": file_sha256(source),
        "model": config["model"],
        "dataset_id": config["dataset_id"],
        "dataset_split": config.get("dataset_split", "test"),
        "dataset_difficulty_sent_to_model": False,
        "model_results_used": False,
        "random_seed": config["random_seed"],
        "config_sha256": file_sha256(Path(config["_config_path"])),
        "prompt_hashes": {
            path.name: file_sha256(path) for path in sorted((ROOT / "prompts").glob("*.md"))
        },
        "schema_hashes": {
            path.name: file_sha256(path) for path in sorted((ROOT / "schemas").glob("*.json"))
        },
        "output_hashes": {path.name: file_sha256(path) for path in outputs if path.exists()},
    }
    output = output_dir / "_run_manifest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def run_until_human_review(config: dict[str, Any], llm: StructuredLLM) -> dict[str, Path]:
    output_dir = Path(config["output_dir"])
    normalized = output_dir / "normalized_cases.csv"
    enriched = output_dir / "enriched_cases.csv"
    readings = output_dir / "case_demand_analyses.csv"
    resume_analyses = False
    if normalized.exists() and enriched.exists() and readings.exists():
        enriched_rows = read_csv(enriched)
        analysis_rows = read_csv(readings)
        aligned_analyses = (
            len(enriched_rows) == len(analysis_rows)
            and len(enriched_rows) > 0
            and {row["case_id"] for row in enriched_rows}
            == {row["case_id"] for row in analysis_rows}
        )
        if aligned_analyses and any(row.get("pipeline_error") for row in analysis_rows):
            error_count = sum(bool(row.get("pipeline_error")) for row in analysis_rows)
            print(
                f"[resume] Recovering {error_count} analyses from cached model responses; "
                "no new case-analysis calls"
            )
            readings = extract_case_readings(config, llm, enriched)
            analysis_rows = read_csv(readings)
        resume_analyses = aligned_analyses and not any(
            row.get("pipeline_error") for row in analysis_rows
        )
    if resume_analyses:
        print(
            f"[resume] Found {len(analysis_rows)} complete solver-demand analyses; "
            "skipping stages 1-3"
        )
    else:
        print("[1/4] Validating XLSX and CSV round trip")
        normalized = validate_and_convert_xlsx(config)
        print("[2/4] Joining all 150 cases to SWE-bench Verified")
        enriched = enrich_with_verified_dataset(config, normalized)
        print("[3/4] Reconstructing the work required from a solver")
        readings = extract_case_readings(config, llm, enriched)
    print("[4/4] Deriving task-shape profiles from the completed analyses")
    classifications = build_task_profile_classifications(config)
    profile_review = generate_task_profile_review(config)
    review_queue = build_review_queue(config)
    summary = generate_summary(config)
    manifest = write_manifest(
        config,
        [normalized, enriched, readings, classifications, profile_review, review_queue, summary],
    )
    return {
        "normalized_cases": normalized,
        "enriched_cases": enriched,
        "case_demand_analyses": readings,
        "case_classifications": classifications,
        "task_profile_review": profile_review,
        "review_queue": review_queue,
        "summary_report": summary,
        "manifest": manifest,
    }


def run_after_freeze(config: dict[str, Any], llm: StructuredLLM) -> dict[str, Path]:
    print("[1/3] Reclassifying every case with the frozen taxonomy")
    classifications = classify_all(config, llm)
    print("[2/3] Building the human-review queue")
    review = build_review_queue(config)
    print("[3/3] Writing the summary report")
    summary = generate_summary(config)
    manifest = write_manifest(
        config,
        [
            Path(config["output_dir"]) / "normalized_cases.csv",
            Path(config["output_dir"]) / "enriched_cases.csv",
            Path(config["output_dir"]) / "solver_demand_codebook_frozen.csv",
            classifications,
            review,
            summary,
        ],
    )
    return {
        "case_classifications": classifications,
        "review_queue": review,
        "summary_report": summary,
        "manifest": manifest,
    }
