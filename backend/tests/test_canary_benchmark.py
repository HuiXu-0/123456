from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.canary_benchmark import CanaryBenchmark, load_canary_dataset


DATASET = Path(__file__).resolve().parents[1] / "evaluation" / "trusted_rag_shadow_dataset.json"
BASELINE = Path(__file__).resolve().parents[1] / "evaluation" / "legacy_rag_baseline.json"


def _case(case_id: str, *, answerable: bool = True) -> dict:
    return {
        "caseId": case_id,
        "query": "fixture query",
        "category": "concept_explanation" if answerable else "no_answer_refusal",
        "standardAnswer": "fixture answer",
        "expectedEvidence": [] if not answerable else [{"documentId": "doc_a"}],
        "answerable": answerable,
        "refusal": not answerable,
        "correctnessCriteria": {"requiredConcepts": ["核心概念"] if answerable else []},
        "evidenceCoverageCriteria": {
            "requiredDocumentIds": ["doc_a"] if answerable else [],
            "minimumMatchedDocuments": 1 if answerable else 0,
        },
        "difficulty": "easy",
        "datasetVersion": "fixture-v1",
    }


def _evidence(document_id: str = "doc_a", content: str = "这里包含核心概念") -> list[dict]:
    return [{"id": "chunk_a", "content": content, "metadata": {"document_id": document_id}}]


def test_loads_task004_a_dataset_and_preserves_non_production_provenance():
    metadata, cases = load_canary_dataset(DATASET)

    assert metadata == {
        "datasetVersion": "trusted-rag-shadow-representative-v1",
        "datasetType": "representative_evaluation_dataset",
        "productionShadowData": False,
        "caseCount": 100,
    }
    assert len(cases) == 100


def test_pair_benchmark_aggregates_p50_p95_p99_rates_and_quality():
    times = iter([0.00, 0.01, 0.01, 0.03, 0.03, 0.06, 0.06, 0.10])
    benchmark = CanaryBenchmark(timeout_ms=25, clock=lambda: next(times))
    cases = [_case("answerable"), _case("refusal", answerable=False)]

    def legacy(case: dict) -> list[dict]:
        return _evidence() if case["answerable"] else []

    def trusted(case: dict) -> list[dict]:
        if case["answerable"]:
            raise TimeoutError("sensitive exception text must not be stored")
        return _evidence("wrong", "irrelevant")

    report = benchmark.run_pair(cases, legacy_runner=legacy, trusted_runner=trusted)

    assert report["legacy"]["latencyMs"] == {
        "mean": 25.0, "p50": 10.0, "p95": 40.0, "p99": 40.0
    }
    assert report["trusted"]["latencyMs"] == {
        "mean": 25.0, "p50": 20.0, "p95": 30.0, "p99": 30.0
    }
    assert report["legacy"]["timeoutRate"] == 0.5
    assert report["legacy"]["errorRate"] == 0.0
    assert report["legacy"]["evidenceCoverage"] == 1.0
    assert report["legacy"]["correctness"] == 1.0
    assert report["trusted"]["timeoutRate"] == 1.0
    assert report["trusted"]["errorRate"] == 0.5
    assert report["trusted"]["evidenceCoverage"] == 0.0
    assert report["trusted"]["correctness"] == 0.0
    assert report["trusted"]["caseResults"][0]["errorType"] == "TimeoutError"
    assert "sensitive exception" not in str(report)


def test_correctness_requires_both_expected_documents_and_required_concepts():
    benchmark = CanaryBenchmark(clock=iter([0.0, 0.001, 0.001, 0.002]).__next__)
    case = _case("quality")
    report = benchmark.run_pair(
        [case],
        legacy_runner=lambda _: _evidence(content="没有必需术语"),
        trusted_runner=lambda _: _evidence(),
    )

    assert report["legacy"]["evidenceCoverage"] == 1.0
    assert report["legacy"]["conceptCoverage"] == 0.0
    assert report["legacy"]["correctness"] == 0.0
    assert report["trusted"]["correctness"] == 1.0


def test_invalid_inputs_fail_closed():
    with pytest.raises(ValueError, match="timeout_ms"):
        CanaryBenchmark(timeout_ms=0)
    with pytest.raises(ValueError, match="cases"):
        CanaryBenchmark().run_pair([], legacy_runner=lambda _: [], trusted_runner=lambda _: [])
    with pytest.raises(ValueError, match="rounds"):
        CanaryBenchmark().run_pair(
            [_case("x")], legacy_runner=lambda _: [], trusted_runner=lambda _: [], rounds=0
        )


def test_committed_baseline_records_environment_and_both_systems_without_production_claim():
    report = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert report["schemaVersion"] == "trusted-rag-canary-benchmark-v1"
    assert report["benchmarkType"] == "offline_representative_evaluation"
    assert report["productionPerformance"] is False
    environment = report["environment"]
    assert environment["datasetVersion"] == "trusted-rag-shadow-representative-v1"
    assert environment["productionShadowData"] is False
    assert environment["embeddingProfile"]["mode"] in {"real_embedding", "hash_fallback"}
    assert environment["embeddingProfile"]["profileId"]
    assert environment["collection"]
    assert environment["runtime"]["python"]
    assert environment["runtime"]["platform"]
    for system in ("legacy", "trusted"):
        result = report[system]
        assert result["caseCount"] == result["sampleCount"] == 100
        assert set(result["latencyMs"]) == {"mean", "p50", "p95", "p99"}
        assert 0.0 <= result["timeoutRate"] <= 1.0
        assert 0.0 <= result["errorRate"] <= 1.0
        assert 0.0 <= result["evidenceCoverage"] <= 1.0
        assert 0.0 <= result["correctness"] <= 1.0
