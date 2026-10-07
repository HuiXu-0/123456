from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "trusted_rag_shadow_dataset.json"
)
EXPECTED_VERSION = "trusted-rag-shadow-representative-v1"
EXPECTED_CATEGORIES = {
    "concept_explanation",
    "process_explanation",
    "code_technical",
    "multi_hop_reasoning",
    "no_answer_refusal",
}
REQUIRED_CASE_FIELDS = {
    "caseId",
    "query",
    "category",
    "standardAnswer",
    "expectedEvidence",
    "answerable",
    "refusal",
    "correctnessCriteria",
    "evidenceCoverageCriteria",
    "difficulty",
    "qualityLabel",
    "datasetVersion",
}


def _dataset() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_dataset_is_explicitly_representative_not_production_shadow_data():
    dataset = _dataset()

    assert dataset["datasetVersion"] == EXPECTED_VERSION
    assert dataset["datasetType"] == "representative_evaluation_dataset"
    assert dataset["productionShadowData"] is False
    assert dataset["provenance"]["containsProductionQueries"] is False
    assert "不是生产 Shadow 请求" in dataset["description"]


def test_dataset_has_exactly_twenty_cases_for_each_required_category():
    dataset = _dataset()
    cases = dataset["cases"]
    counts = Counter(case["category"] for case in cases)

    assert dataset["caseCount"] == len(cases) == 100
    assert set(counts) == EXPECTED_CATEGORIES
    assert counts == Counter({category: 20 for category in EXPECTED_CATEGORIES})
    assert {
        category: requirement["requiredCount"]
        for category, requirement in dataset["categoryRequirements"].items()
    } == {category: 20 for category in EXPECTED_CATEGORIES}


def test_every_case_has_complete_versioned_quality_and_evidence_contract():
    cases = _dataset()["cases"]
    case_ids = [case["caseId"] for case in cases]

    assert len(case_ids) == len(set(case_ids))
    for case in cases:
        assert REQUIRED_CASE_FIELDS <= set(case)
        assert case["datasetVersion"] == EXPECTED_VERSION
        assert case["query"].strip()
        assert case["standardAnswer"].strip()
        assert case["difficulty"] in {"easy", "medium", "hard"}
        assert case["answerable"] is not case["refusal"]
        assert case["correctnessCriteria"]["gradingRule"].strip()
        assert case["evidenceCoverageCriteria"]["gradingRule"].strip()
        assert case["qualityLabel"]["groundingRequired"] is True
        assert case["qualityLabel"]["annotationMethod"] == (
            "repository_grounded_representative_curation"
        )
        assert case["qualityLabel"]["reviewStatus"] == (
            "pending_independent_human_review"
        )


def test_answerable_and_refusal_cases_have_fail_closed_evidence_rules():
    for case in _dataset()["cases"]:
        evidence = case["expectedEvidence"]
        coverage = case["evidenceCoverageCriteria"]
        required_concepts = case["correctnessCriteria"]["requiredConcepts"]

        if case["answerable"]:
            assert not case["refusal"]
            assert evidence
            assert required_concepts
            assert coverage["minimumMatchedDocuments"] >= 1
            assert coverage["requiredDocumentIds"]
            for item in evidence:
                assert item["documentId"] in coverage["requiredDocumentIds"]
                source = Path(__file__).resolve().parents[2] / item["sourcePath"]
                assert source.is_file()
                assert item["support"].strip()
        else:
            assert case["category"] == "no_answer_refusal"
            assert case["refusal"]
            assert evidence == []
            assert required_concepts == []
            assert coverage["requiredDocumentIds"] == []
            assert coverage["minimumMatchedDocuments"] == 0
            assert case["qualityLabel"]["expectedOutcome"] == "refuse"


def test_representative_queries_do_not_contain_obvious_phone_or_email_pii():
    rendered_queries = "\n".join(case["query"] for case in _dataset()["cases"])

    assert re.search(r"\b1[3-9]\d{9}\b", rendered_queries) is None
    assert re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", rendered_queries) is None
