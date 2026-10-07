from app.core.llm_tutor_mock import (
    REMEDIAL_TYPES,
    build_remedial_suggestions,
    identify_problem,
    tutor_reply,
)


def test_tutor_reply_keeps_keyword_chain_and_fallback():
    reply, suggestions = tutor_reply("为什么需要激活函数？")
    assert "等价于什么" in reply
    assert suggestions == ["等价于线性变换", "可以拟合任意函数", "不确定"]

    fallback, fallback_suggestions = tutor_reply("我还是不明白")
    assert "汇总" in fallback
    assert fallback_suggestions == ["加权求和", "取最大值", "不确定"]


def test_identify_problem_matches_keywords_and_falls_back():
    assert identify_problem("卷积核和池化怎么配合？", "卷积神经网络") == "卷积与池化"
    assert identify_problem("这里为什么？", "Transformer") == "Transformer的核心概念"


def test_remedial_suggestions_follow_public_type_order_and_contract():
    suggestions = build_remedial_suggestions("自注意力机制")
    assert [item["type"] for item in suggestions] == list(REMEDIAL_TYPES)
    assert [item["id"] for item in suggestions] == [f"r-{item}" for item in REMEDIAL_TYPES]
    assert all("自注意力机制" in item["title"] for item in suggestions)
    assert all(set(item) == {"id", "type", "title", "expect"} for item in suggestions)
