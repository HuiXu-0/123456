import pytest

from app.core import llm as llm_module
from app.core.llm_deepseek import LLMGenerationError
from app.core.llm_feynman import (
    FEYNMAN_BLOCKING,
    FEYNMAN_CONCEPTS,
    SEVERITY_RANK,
    clean_feynman,
    evaluate_mock,
)


def test_llm_module_keeps_feynman_compatibility_aliases():
    assert llm_module._FEYNMAN_CONCEPTS is FEYNMAN_CONCEPTS
    assert llm_module._SEVERITY_RANK is SEVERITY_RANK
    assert llm_module._FEYNMAN_BLOCKING is FEYNMAN_BLOCKING


def test_feynman_concept_catalog_keeps_six_core_topics_and_four_concepts_each():
    assert set(FEYNMAN_CONCEPTS) == {"nn", "ml", "dl", "cnn", "transformer", "finetune"}
    assert all(len(concepts) == 4 for concepts in FEYNMAN_CONCEPTS.values())
    assert SEVERITY_RANK == {"high": 0, "medium": 1, "low": 2}
    assert FEYNMAN_BLOCKING == ("high", "medium")


def test_mock_feynman_scores_coverage_and_orders_gaps_by_severity():
    result = evaluate_mock("nn", "神经网络", "激活函数引入非线性，输入先进行加权求和")
    assert result["score"] == 50
    assert result["complete"] is False
    assert [gap["severity"] for gap in result["gaps"]] == ["medium", "low"]
    assert result["gaps"][0]["title"] == "反向传播与梯度更新"
    assert len(result["followups"]) == 2


def test_mock_feynman_complete_when_all_concepts_are_covered():
    result = evaluate_mock(
        "nn",
        "神经网络",
        "输入与权重加权求和后加偏置，再用 ReLU 激活引入非线性，最后反向传播梯度更新。",
    )
    assert result["score"] == 100
    assert result["gaps"] == []
    assert result["followups"] == []
    assert result["complete"] is True


def test_mock_feynman_unknown_topic_keeps_long_and_short_fallback_contracts():
    long_result = evaluate_mock("custom", "图搜索", "这是一段足够长的讲解，描述图节点、边、搜索过程、状态更新、终止条件以及最终路径输出。")
    assert long_result["score"] == 70
    assert long_result["complete"] is True

    short_result = evaluate_mock("custom", "图搜索", "太短")
    assert set(short_result) == {"feedback", "gaps", "followups", "complete"}
    assert short_result["gaps"][0]["kpId"] == "custom"
    assert short_result["complete"] is False


def test_clean_feynman_rewrites_kp_orders_gaps_clamps_score_and_limits_followups():
    result = clean_feynman(
        {
            "feedback": " 继续补充 ",
            "score": 120,
            "gaps": [
                {"kpId": "invented", "title": " 低优先级 ", "detail": " 细节 ", "severity": "low"},
                {"kpId": "invented", "title": "非法严重度", "detail": "说明", "severity": "fatal"},
                {"title": "高优先级", "detail": "说明", "severity": "high"},
                {"title": "缺说明", "severity": "high"},
            ],
            "followups": [" 一 ", "二", "三", "四"],
            "complete": "yes",
        },
        "nn",
    )
    assert [gap["severity"] for gap in result["gaps"]] == ["high", "medium", "low"]
    assert all(gap["kpId"] == "nn" for gap in result["gaps"])
    assert result["score"] == 100
    assert result["followups"] == ["一", "二", "三"]
    assert result["complete"] is False


def test_clean_feynman_derives_score_and_requires_feedback():
    result = clean_feynman(
        {
            "feedback": "需要补充",
            "gaps": [
                {"title": "缺口1", "detail": "说明", "severity": "high"},
                {"title": "缺口2", "detail": "说明", "severity": "medium"},
            ],
        },
        "nn",
    )
    assert result["score"] == 50
    assert result["complete"] is False

    with pytest.raises(LLMGenerationError, match="费曼评估缺 feedback"):
        clean_feynman({"gaps": []}, "nn")
