from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from openpyxl import Workbook

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import pipeline


def make_workbook(path: Path, rows: list[tuple[str, str, str]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["instance_id", "issue", "pr"])
    for row in rows:
        sheet.append(row)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"
    workbook.save(path)


def dataset_record(case_id: str) -> dict[str, Any]:
    return {
        "repo": "repo/project",
        "instance_id": case_id,
        "base_commit": "abc123",
        "patch": "diff --git a/core.py b/core.py\n+ pass nested context",
        "test_patch": "diff --git a/test_core.py b/test_core.py\n+ test nested save",
        "problem_statement": "Nested save returns the outer value.",
        "hints_text": "Inspect the nested handler path.",
        "created_at": "2024-01-01T00:00:00Z",
        "version": "1.0",
        "FAIL_TO_PASS": '["test_core.py::test_nested"]',
        "PASS_TO_PASS": '["test_core.py::test_simple"]',
        "environment_setup_commit": "def456",
        "difficulty": "15 min - 1 hour",
    }


def make_config(tmp_path: Path, rows: list[tuple[str, str, str]]) -> dict[str, Any]:
    source = tmp_path / "cases.xlsx"
    make_workbook(source, rows)
    config_path = tmp_path / "config.yaml"
    raw = {
        "input_xlsx": str(source),
        "output_dir": str(tmp_path / "outputs"),
        "dataset_id": "synthetic/SWE-bench_Verified",
        "dataset_split": "test",
        "random_seed": 7,
        "open_coding_sample_size": min(6, len(rows)),
        "validation_sample_size": min(2, max(0, len(rows) - 6)),
        "low_confidence_threshold": 0.7,
        "primary_class_min_cases": 3,
        "primary_class_min_count": 6,
        "primary_class_max_count": 12,
        "model": {"name": "mock", "reasoning_effort": None, "max_retries": 1, "concurrency": 2},
        "normalization": {
            "header_aliases": {"instance_id": "case_id"},
            "remove_obvious_github_navigation": True,
        },
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = pipeline.load_config(config_path)
    config["_dataset_records"] = [dataset_record(row[0]) for row in rows]
    return config


PRIMARY_CODES = [
    "TRACE_ACROSS_ABSTRACTIONS",
    "RECONSTRUCT_IMPLICIT_CONTRACT",
    "LOCALIZE_CROSS_COMPONENT_BEHAVIOR",
    "REASON_ABOUT_REPRESENTATION_CHANGES",
    "DESIGN_COMPATIBLE_CHANGE",
    "BUILD_DISCRIMINATING_REGRESSION",
]


def mock_codebook() -> dict[str, Any]:
    motivating = ["repo__case-1", "repo__case-2", "repo__case-3"]
    return {
        "primary_classes": [
            {
                "code": code,
                "definition": f"Solver must perform reusable work represented by {code}.",
                "solver_actions": ["Inspect related abstractions and test a behavioral hypothesis."],
                "inclusion_criteria": ["Success requires this solver action."],
                "exclusion_criteria": ["The answer is directly stated at the failing line."],
                "neighbor_distinctions": ["Distinguished by the dominant solver action."],
                "motivating_case_ids": motivating,
            }
            for code in PRIMARY_CODES
        ],
    }


class MockLLM:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def complete(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        instructions: str,
        input_text: str,
        cache_scope: str,
    ) -> dict[str, Any]:
        self.inputs.append(input_text)
        if schema_name == "solver_demand_codebook":
            return mock_codebook()
        evidence = [
            {"source": "problem_statement", "quote": "Nested save returns the outer value."},
            {"source": "patch", "quote": "+ pass nested context"},
        ]
        if schema_name == "case_demand_analysis":
            return {
                "task_summary": "Make nested saving preserve the intended context.",
                "expected_outcome": "Nested save uses the nested value.",
                "solver_starting_information": "The report contrasts nested and ordinary saving.",
                "requirement_or_behavior_ambiguity": None,
                "repository_knowledge_needed": ["handler delegation and context ownership"],
                "diagnostic_work_required": ["trace context through nested handler calls"],
                "implementation_work_required": ["correct the context handoff"],
                "verification_work_required": ["compare nested and non-nested cases"],
                "root_cause_confirmed_by_patch": "The outer context reaches the nested handler.",
                "solution_confirmed_by_patch": "Pass the nested context.",
                "interpretation_demand": "LOW",
                "diagnosis_demand": "HIGH",
                "implementation_demand": "LOW",
                "verification_demand": "MEDIUM",
                "dominant_demand": "DIAGNOSIS",
                "provisional_capability_phrases": ["trace behavior across abstraction boundaries"],
                "evidence": evidence,
                "confidence": 0.92,
                "uncertainty": None,
            }
        return {
            "task_summary": "Make nested saving preserve the intended context.",
            "primary_solver_demand_class": "TRACE_ACROSS_ABSTRACTIONS",
            "interpretation_demand": "LOW",
            "diagnosis_demand": "HIGH",
            "implementation_demand": "LOW",
            "verification_demand": "MEDIUM",
            "dominant_demand": "DIAGNOSIS",
            "repository_knowledge_needed": ["handler delegation and context ownership"],
            "diagnostic_work_required": ["trace context through nested handler calls"],
            "implementation_work_required": ["correct the context handoff"],
            "verification_work_required": ["compare nested and non-nested cases"],
            "root_cause_summary": "The outer context reaches the nested handler.",
            "solution_summary": "Pass the nested context.",
            "evidence": evidence,
            "confidence": 0.92,
            "classification_status": "CLASSIFIED",
            "uncertainty_reason": None,
        }


SYNTHETIC_ROWS = [
    (
        f"repo__case-{number}",
        "Skip to content\nrepo\nRepository navigation\nCode\nIssues\nInsights\nNested save returns the outer value.",
        "Skip to content\nrepo\nRepository navigation\nCode\nPull requests\nInsights\nPass the nested context.",
    )
    for number in range(1, 13)
]


def test_xlsx_csv_round_trip_preserves_multiline_unicode_and_quotes(tmp_path: Path) -> None:
    rows = [("repo__case-1", "line 1\nline 2, \"quoted\" 한글", "=literal text")]
    config = make_config(tmp_path, rows)
    result = pipeline.read_csv(pipeline.validate_and_convert_xlsx(config))[0]
    assert result["raw_issue"] == rows[0][1]
    assert result["raw_pr"] == rows[0][2]
    assert "ALIASED_INSTANCE_ID_TO_CASE_ID" in result["validation_warnings"]


def test_verified_join_is_exact_and_preserves_hidden_difficulty(tmp_path: Path) -> None:
    config = make_config(tmp_path, SYNTHETIC_ROWS[:2])
    normalized = pipeline.validate_and_convert_xlsx(config)
    enriched = pipeline.read_csv(pipeline.enrich_with_verified_dataset(config, normalized))
    assert len(enriched) == 2
    assert enriched[0]["problem_statement"] == "Nested save returns the outer value."
    assert enriched[0]["dataset_difficulty_reference"] == "15 min - 1 hour"
    prompt = pipeline._case_input(enriched[0])
    assert "SWE-BENCH PROBLEM_STATEMENT (REQUIRED READING)" in prompt
    assert "Nested save returns the outer value." in prompt
    assert "15 min - 1 hour" not in prompt


def test_verified_join_rejects_missing_instance(tmp_path: Path) -> None:
    config = make_config(tmp_path, SYNTHETIC_ROWS[:2])
    config["_dataset_records"] = config["_dataset_records"][:1]
    normalized = pipeline.validate_and_convert_xlsx(config)
    with pytest.raises(pipeline.PipelineError, match="do not match"):
        pipeline.enrich_with_verified_dataset(config, normalized)


def test_codebook_requires_broad_classes_and_three_cases(tmp_path: Path) -> None:
    config = make_config(tmp_path, SYNTHETIC_ROWS)
    bad = mock_codebook()
    bad["primary_classes"] = bad["primary_classes"][:1]
    with pytest.raises(pipeline.PipelineError, match="6-12"):
        pipeline.validate_codebook_payload(bad, config)
    bad = mock_codebook()
    bad["primary_classes"][0]["motivating_case_ids"] = ["one"]
    with pytest.raises(pipeline.PipelineError, match="too short|at least 3"):
        pipeline.validate_codebook_payload(bad, config)


def test_unmatched_evidence_is_preserved_as_a_review_warning() -> None:
    case = dataset_record("repo__case-1") | {"raw_issue": "exact", "raw_pr": "exact"}
    warnings = pipeline.validate_evidence(
        {"evidence": [{"source": "problem_statement", "quote": "invented"}]}, case
    )
    assert warnings == ["UNMATCHED_problem_statement: invented"]


def test_smart_quotes_and_ellipsis_can_match_source_evidence() -> None:
    source = "The report says nested saving fails, and then shows the expected result."
    assert pipeline._evidence_quote_matches(
        "“The report says nested saving fails...the expected result.”", source
    )


def test_task_shape_pipeline_uses_existing_demand_analysis(tmp_path: Path) -> None:
    config = make_config(tmp_path, SYNTHETIC_ROWS)
    llm = MockLLM()
    before = pipeline.run_until_human_review(config, llm)
    assert before["enriched_cases"].exists()
    assert before["case_demand_analyses"].exists()
    assert before["task_profile_review"].exists()
    assert all("dataset_difficulty_reference" not in input_text for input_text in llm.inputs)
    classifications = pipeline.read_csv(before["case_classifications"])
    assert len(classifications) == len(SYNTHETIC_ROWS)
    assert all(row["overall_demand"] == "HARD" for row in classifications)
    assert all(row["solver_profile"] == "DIAGNOSIS_LED" for row in classifications)
    assert "Solver profiles" in before["summary_report"].read_text(encoding="utf-8")
    manifest = json.loads(before["manifest"].read_text(encoding="utf-8"))
    assert manifest["dataset_difficulty_sent_to_model"] is False
    assert manifest["model_results_used"] is False


@pytest.mark.parametrize(
    ("ratings", "expected"),
    [
        (("LOW", "LOW", "LOW", "LOW"), ("EASY", "STRAIGHTFORWARD")),
        (
            ("MEDIUM", "MEDIUM", "LOW", "MEDIUM"),
            ("MODERATE", "LOCAL_CHANGE_REQUIRING_REASONING"),
        ),
        (("LOW", "HIGH", "LOW", "MEDIUM"), ("HARD", "DIAGNOSIS_LED")),
        (("LOW", "HIGH", "HIGH", "MEDIUM"), ("HARD", "BALANCED_MULTI_STAGE")),
    ],
)
def test_task_shape_derivation(
    ratings: tuple[str, str, str, str], expected: tuple[str, str]
) -> None:
    interpretation, diagnosis, implementation, verification = ratings
    assert pipeline.derive_task_shape(
        {
            "interpretation_demand": interpretation,
            "diagnosis_demand": diagnosis,
            "implementation_demand": implementation,
            "verification_demand": verification,
        }
    ) == expected
