import pytest

from app.core import llm as llm_module
from app.core.llm_scoring import (
    character_bigrams,
    clamp_score,
    credibility_score,
    point_coverage,
)


def test_llm_module_keeps_scoring_compatibility_aliases():
    assert llm_module._char_bigrams is character_bigrams
    assert llm_module._point_coverage is point_coverage
    assert llm_module._credibility_of is credibility_score
    assert llm_module._clamp_score is clamp_score


def test_character_bigrams_ignore_case_and_whitespace():
    assert character_bigrams("A b C") == {"ab", "bc"}
    assert character_bigrams("") == set()


def test_point_coverage_handles_exact_partial_and_empty_points():
    assert point_coverage("反向传播", "反向传播通过链式法则更新权重") == 1.0
    assert 0.0 < point_coverage("反向传播", "反向计算") < 1.0
    assert point_coverage("", "任意回答") == 0.0


@pytest.mark.parametrize(
    ("source", "url", "expected"),
    [
        ("arXiv", "https://arxiv.org/abs/1", 97),
        ("Stanford", "https://cs231n.stanford.edu", 95),
        ("PyTorch", "https://pytorch.org/docs", 93),
        ("3Blue1Brown", "https://youtube.com/watch?v=1", 90),
        ("普通博客", "https://example.com", 82),
    ],
)
def test_credibility_score_preserves_existing_source_tiers(source, url, expected):
    assert credibility_score(source, url) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [(120, 0, 100), (-1, 0, 0), ("75", 0, 75), (None, 42, 42), ("bad", 30, 30)],
)
def test_clamp_score_bounds_and_fallback(value, default, expected):
    assert clamp_score(value, default) == expected
