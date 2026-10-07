from collections import Counter
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.rag.trusted_rag_validation import (
    TrustedRAGValidationDataset,
    build_validation_dataset,
    query_fingerprint,
)


def test_validation_dataset_has_125_balanced_independent_cases() -> None:
    dataset = build_validation_dataset()
    assert len(dataset.cases) == 125
    assert set(Counter(case.query_type for case in dataset.cases).values()) == {25}
    source = json.loads(
        (Path(__file__).parents[1] / "evaluation" / "trusted_rag_shadow_dataset.json")
        .read_text(encoding="utf-8")
    )
    e3_fingerprints = {query_fingerprint(case["query"]) for case in source["cases"]}
    assert dataset.query_fingerprints().isdisjoint(e3_fingerprints)
    assert dataset.tuning_dataset_reused is False


def test_validation_dataset_rejects_duplicate_queries() -> None:
    payload = build_validation_dataset().model_dump(mode="json")
    payload["cases"][1]["query"] = payload["cases"][0]["query"]
    with pytest.raises(ValidationError, match="normalized query must be unique"):
        TrustedRAGValidationDataset.model_validate(payload)


def test_validation_dataset_rejects_underrepresented_stratum() -> None:
    payload = build_validation_dataset().model_dump(mode="json")
    payload["cases"] = [
        case for case in payload["cases"] if case["query_type"] != "method_comparison"
    ]
    with pytest.raises(ValidationError, match="at least 20"):
        TrustedRAGValidationDataset.model_validate(payload)
