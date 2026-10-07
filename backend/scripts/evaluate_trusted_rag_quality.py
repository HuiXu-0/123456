"""Evaluate 100 frozen Shadow requests without persisting query or knowledge text."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.rag.embeddings import Embedder, EmbeddingProfile, set_embedder_for_evaluation
from app.rag.evidence_quality_evaluation import (
    EvidenceEvaluationRecord,
    QualityEvaluationResult,
)
from app.rag.pipeline import TrustedRetrievalPipeline
from app.rag.retriever import HybridRetriever
from app.rag.shadow_admission import ShadowEvaluationDataset
from app.rag.vector_store import _ChromaStore


QUERY_TYPE_RULES: dict[str, tuple[str, ...] | str] = {
    "concept_explanation": "concept_explanation",
    "operation_steps": "process_explanation",
    "programming_practice": "code_technical",
    "comprehensive_question": "multi_hop_reasoning",
    "method_comparison": (
        "t004a_concept_explanation_05", "t004a_concept_explanation_07",
        "t004a_concept_explanation_08", "t004a_concept_explanation_20",
        "t004a_process_explanation_10", "t004a_process_explanation_11",
        "t004a_process_explanation_12", "t004a_process_explanation_13",
        "t004a_process_explanation_14", "t004a_process_explanation_15",
        "t004a_process_explanation_16", "t004a_process_explanation_17",
        "t004a_process_explanation_18", "t004a_code_technical_03",
        "t004a_code_technical_04", "t004a_code_technical_05",
        "t004a_code_technical_07", "t004a_code_technical_09",
        "t004a_code_technical_11", "t004a_code_technical_12",
    ),
}


def _request_id(query_type: str, case_id: str, ordinal: int) -> str:
    raw = f"trusted-rag-shadow-e3a|{query_type}|{case_id}|{ordinal}"
    return "shadow_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _sampling_plan(cases: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id = {case["caseId"]: case for case in cases}
    for case in cases:
        grouped[case["category"]].append(case)
    plan: list[tuple[str, dict[str, Any]]] = []
    for query_type, rule in QUERY_TYPE_RULES.items():
        selected = [by_id[item] for item in rule] if isinstance(rule, tuple) else grouped[rule]
        plan.extend((query_type, case) for case in selected[:20])
    return plan


def _score_completeness(content: str, required_concepts: list[str]) -> int:
    if not required_concepts:
        return 3
    ratio = sum(concept.lower() in content.lower() for concept in required_concepts) / len(required_concepts)
    return 3 if ratio == 1 else 2 if ratio >= 0.5 else 1 if ratio > 0 else 0


def _source_quality(source: dict[str, Any]) -> int:
    if source.get("documentId") and (source.get("title") or source.get("location")):
        return 3
    if source.get("documentId"):
        return 2
    return 1 if source else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="TASK-004-E3-B Evidence quality evaluation")
    parser.add_argument("--source-dataset", default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_shadow_dataset.json"))
    parser.add_argument("--shadow-dataset", default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_shadow_e3a_frozen.json"))
    parser.add_argument("--output", default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_quality_e3b.json"))
    parser.add_argument("--collection", default="kb_chunks")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    source = json.loads(Path(args.source_dataset).read_text(encoding="utf-8"))
    shadow = ShadowEvaluationDataset.model_validate_json(Path(args.shadow_dataset).read_text(encoding="utf-8"))
    plan = _sampling_plan(source["cases"])
    if len(plan) != 100 or len(shadow.samples) != 100:
        raise ValueError("E3-B requires exactly 100 frozen Shadow requests")

    profile = EmbeddingProfile("hash", "deterministic-hash-v1", settings.embedding_dimension)
    set_embedder_for_evaluation(Embedder(profile=profile, allow_fallback=True))
    store = _ChromaStore(settings.chroma_dir, collection=args.collection, profile=profile)
    pipeline = TrustedRetrievalPipeline(retriever=HybridRetriever(store_getter=lambda: store))
    records: list[EvidenceEvaluationRecord] = []
    expected_request_ids: set[str] = set()
    for ordinal, (query_type, case) in enumerate(plan, start=1):
        request_id = _request_id(query_type, case["caseId"], ordinal)
        expected_request_ids.add(request_id)
        expected_docs = set(case["evidenceCoverageCriteria"]["requiredDocumentIds"])
        concepts = list(case["correctnessCriteria"]["requiredConcepts"])
        response = pipeline.execute({"query": case["query"], "user_id": "offline-quality-review", "top_k": args.top_k})
        for rank, evidence in enumerate(response.evidence, start=1):
            document_id = evidence.source.get("documentId")
            supports = document_id in expected_docs
            records.append(EvidenceEvaluationRecord(
                request_id=request_id,
                evidence_rank=rank,
                relevance_score=3 if supports else 0,
                completeness_score=_score_completeness(evidence.content, concepts),
                supports_answer=supports,
                source_quality=_source_quality(evidence.source),
            ))

    aggregate = shadow.aggregate()
    result = QualityEvaluationResult.from_records(
        evaluation_window=shadow.evaluation_window,
        evidence_overlap=aggregate.evidence_overlap_mean or 0.0,
        source_coverage=aggregate.source_coverage_mean or 0.0,
        records=records,
        expected_request_ids=expected_request_ids,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    for forbidden in ('"query"', '"knowledge_content"', '"user_identity"', '"content"'):
        if forbidden in rendered:
            raise ValueError(f"privacy field found in output: {forbidden}")
    output.write_text(rendered, encoding="utf-8")
    print(json.dumps(result.model_dump(exclude={"records"}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
