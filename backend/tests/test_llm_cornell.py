import pytest

from app.core import llm as llm_module
from app.core.llm_cornell import (
    CORNELL_TEMPLATES,
    assemble_cornell,
    clean_cornell,
    generate_mock_cornell,
)
from app.core.llm_deepseek import LLMGenerationError

SOURCES = [{"title": "教材", "type": "教材", "confidence": 0.9}]


def test_llm_module_keeps_cornell_template_compatibility_alias():
    assert llm_module._CORNELL_TEMPLATES is CORNELL_TEMPLATES


def test_cornell_template_catalog_keeps_six_core_topics():
    assert set(CORNELL_TEMPLATES) == {"nn", "ml", "dl", "cnn", "transformer", "finetune"}
    for template in CORNELL_TEMPLATES.values():
        assert 5 <= len(template["cues"]) <= 8
        assert template["outline"]
        assert template["summaryHint"]


def test_assemble_cornell_numbers_items_links_questions_and_copies_sources():
    result = assemble_cornell(
        [("question", "问题"), ("keyword", "关键词")],
        [("第一部分", ["要点"]), ("第二部分", ["要点2"])],
        "总结引导",
        SOURCES,
    )
    assert result["cues"] == [
        {"id": "c1", "type": "question", "text": "问题"},
        {"id": "c2", "type": "keyword", "text": "关键词"},
    ]
    assert result["noteOutline"][0]["cueId"] == "c1"
    assert result["noteOutline"][1]["cueId"] is None
    result["sources"][0]["title"] = "已修改"
    assert SOURCES[0]["title"] == "教材"


def test_mock_cornell_uses_fixed_template_and_generic_fallback():
    fixed = generate_mock_cornell("nn", "神经网络", "初级", "", SOURCES)
    assert fixed["cues"][0]["text"] == "为什么神经网络需要激活函数？"
    assert fixed["summaryHint"] == CORNELL_TEMPLATES["nn"]["summaryHint"]

    generic = generate_mock_cornell("custom", "图搜索", "高级", "图上的路径搜索", SOURCES)
    assert len(generic["cues"]) == 5
    assert all("图搜索" in cue["text"] for cue in generic["cues"])
    assert generic["noteOutline"][0]["points"][0] == "图上的路径搜索"


def test_clean_cornell_enforces_limits_enums_and_links():
    data = {
        "cues": [
            {"type": "keyword" if index == 1 else "invalid", "text": f" 线索{index} "}
            for index in range(10)
        ],
        "noteOutline": [
            {"heading": " 第一部分 ", "points": [" a ", "b", "c", "d", "e"]},
            {"heading": "第二部分", "points": ["p"]},
        ],
        "summaryHint": " 总结一下 ",
    }
    result = clean_cornell(data, SOURCES)
    assert len(result["cues"]) == 8
    assert result["cues"][0]["type"] == "question"
    assert result["cues"][1]["type"] == "keyword"
    assert result["noteOutline"][0]["cueId"] == "c1"
    assert result["noteOutline"][1]["cueId"] is None
    assert result["noteOutline"][0]["points"] == ["a", "b", "c", "d"]
    assert result["summaryHint"] == "总结一下"


@pytest.mark.parametrize(
    "data, message",
    [
        ({}, "康奈尔线索缺 cues 数组"),
        ({"cues": [{"type": "question", "text": "只有一条"}]}, "有效条目不足 5 条"),
    ],
)
def test_clean_cornell_rejects_invalid_or_insufficient_cues(data, message):
    with pytest.raises(LLMGenerationError, match=message):
        clean_cornell(data, SOURCES)
