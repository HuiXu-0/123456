from app.core.llm import (
    _doc_key_sentences as compatibility_key_sentences,
    _mock_doc_answer as compatibility_doc_answer,
)
from app.core.llm_document_mock import answer_document_question, extract_key_sentences


def test_llm_module_keeps_document_mock_compatibility_aliases():
    assert compatibility_key_sentences is extract_key_sentences
    assert compatibility_doc_answer is answer_document_question


def test_extract_key_sentences_filters_duplicates_short_text_and_limits_count():
    contexts = [
        "短句。ZetaVec 通过双阶段归一化保持向量稳定。重复要点用于验证去重。",
        "重复要点用于验证去重。第二个有效要点用于验证顺序。第三个有效要点不会返回。",
    ]
    assert extract_key_sentences(contexts, max_n=3) == [
        "ZetaVec 通过双阶段归一化保持向量稳定。",
        "重复要点用于验证去重。",
        "第二个有效要点用于验证顺序。",
    ]


def test_extract_key_sentences_truncates_each_item_to_120_characters():
    sentence = "长" * 140 + "。"
    assert extract_key_sentences([sentence], max_n=1) == ["长" * 120]


def test_document_answer_uses_only_context_and_numbered_citations():
    answer = answer_document_question(
        "测试文档",
        ["第一条文档事实足够长且可被抽取。第二条文档事实也足够长且可被抽取。"],
        "请总结核心内容",
    )
    assert "第一条文档事实" in answer and "[1]" in answer
    assert "第二条文档事实" in answer and "[2]" in answer
    assert "《测试文档》" in answer


def test_document_answer_fails_closed_without_context():
    answer = answer_document_question("空文档", [], "文档讲了什么？")
    assert "未提及相关内容" in answer
    assert "不做文档之外的推测" in answer
