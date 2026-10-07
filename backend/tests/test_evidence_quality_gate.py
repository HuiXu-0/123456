from app.rag.evidence_quality_gate import EvidenceQualityGate


def _candidate(document_id: str, relative: float, keyword_score: float = 1.0) -> dict:
    return {
        "source": {"documentId": document_id},
        "metadata": {"keywordRelativeScore": relative},
        "keyword_score": keyword_score,
    }


def test_top3_gate_checks_relevance_source_count_and_support():
    gate = EvidenceQualityGate(min_relevance=0.9)
    passed = gate.evaluate([
        _candidate("doc_a", 1.0),
        _candidate("doc_b", 0.95),
        _candidate("doc_c", 0.90),
    ])
    assert passed.passed is True
    assert passed.relevance == 0.95
    assert passed.source_count == 3
    assert passed.support is True


def test_top3_gate_blocks_duplicate_weak_or_unsupported_evidence():
    result = EvidenceQualityGate().evaluate([
        _candidate("doc_a", 1.0),
        _candidate("doc_a", 0.7, keyword_score=0.0),
    ])
    assert result.passed is False
    assert set(result.reasons) == {"relevance", "source_count", "support"}
