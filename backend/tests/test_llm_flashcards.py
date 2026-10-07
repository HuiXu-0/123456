import json

import pytest

from app.core.llm import LLMClient
from app.core.llm_deepseek import LLMGenerationError
from app.core.llm_flashcards import clean_flashcards, generate_mock_flashcards


def test_mock_flashcards_are_deterministic_grounded_and_limited():
    contexts = [
        "泽塔向量收敛定理说明迭代过程稳定。第二个要点描述误差上界。",
        "泽塔向量收敛定理说明迭代过程稳定。第三个要点说明停止条件。",
    ]
    first = generate_mock_flashcards("测试文档", contexts, 2)
    assert first == generate_mock_flashcards("测试文档", contexts, 2)
    assert len(first) == 2
    assert first[0]["back"] == "泽塔向量收敛定理说明迭代过程稳定。"
    assert "测试文档" in first[0]["front"]
    assert all(card["back"] in "".join(contexts) for card in first)


def test_mock_flashcards_empty_document_uses_non_hallucinatory_fallback():
    cards = generate_mock_flashcards("空文档", ["短句"], 5)
    assert cards == [
        {
            "front": "《空文档》的核心内容是什么？",
            "back": "该文档暂未解析到可用于生成闪卡的正文内容，请检查文档是否为纯文本可抽取格式。",
        }
    ]


def test_llm_mock_flashcard_compatibility_wrapper_matches_module():
    contexts = ["这是一个长度足够的文档原文要点。"]
    assert LLMClient._mock_flashcards("文档", contexts, 3) == generate_mock_flashcards(
        "文档", contexts, 3
    )


def test_clean_flashcards_filters_empty_values_strips_and_truncates():
    raw = json.dumps(
        {
            "cards": [
                {"front": " 问题一 ", "back": " 答案一 "},
                {"front": "问题二", "back": "答案二"},
                {"front": "", "back": "无问题"},
                {"front": "问题三", "back": "答案三"},
            ]
        },
        ensure_ascii=False,
    )
    assert clean_flashcards(raw, 2) == [
        {"front": "问题一", "back": "答案一"},
        {"front": "问题二", "back": "答案二"},
    ]


def test_clean_flashcards_accepts_markdown_json_fence():
    raw = '```json\n{"cards":[{"front":"问题","back":"答案"}]}\n```'
    assert clean_flashcards(raw, 8) == [{"front": "问题", "back": "答案"}]


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        json.dumps({"cards": []}),
        json.dumps({"cards": [{"front": "", "back": "答案"}]}),
    ],
)
def test_clean_flashcards_rejects_unusable_output(raw):
    with pytest.raises(LLMGenerationError, match="闪卡输出无法解析为契约 JSON"):
        clean_flashcards(raw, 8)
