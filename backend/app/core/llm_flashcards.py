"""文档闪卡的确定性 Mock 生成与真实输出契约清洗。"""
from __future__ import annotations

from app.core.llm_deepseek import LLMGenerationError
from app.core.llm_document_mock import extract_key_sentences
from app.core.llm_output import extract_json


def generate_mock_flashcards(
    source_title: str, contexts: list[str], count: int
) -> list[dict[str, str]]:
    """从文档要点句确定性生成正/背面闪卡。"""
    sentences = extract_key_sentences(contexts, max_n=count)
    cards: list[dict[str, str]] = []
    for index, sentence in enumerate(sentences, start=1):
        topic = sentence[:14].rstrip("，,。.；;：: ") or f"要点 {index}"
        cards.append(
            {
                "front": f"关于「{topic}」，《{source_title}》是怎么讲的？",
                "back": sentence,
            }
        )
    if not cards:
        cards.append(
            {
                "front": f"《{source_title}》的核心内容是什么？",
                "back": "该文档暂未解析到可用于生成闪卡的正文内容，请检查文档是否为纯文本可抽取格式。",
            }
        )
    return cards


def clean_flashcards(raw: str, count: int) -> list[dict[str, str]]:
    """清洗真实模型闪卡输出，过滤空正/背面并按请求数量截断。"""
    data = extract_json(raw)
    raw_cards = data.get("cards") if isinstance(data, dict) else None
    cards: list[dict[str, str]] = []
    for card in raw_cards or []:
        if (
            isinstance(card, dict)
            and str(card.get("front") or "").strip()
            and str(card.get("back") or "").strip()
        ):
            cards.append(
                {
                    "front": str(card["front"]).strip(),
                    "back": str(card["back"]).strip(),
                }
            )
    if not cards:
        raise LLMGenerationError("闪卡输出无法解析为契约 JSON")
    return cards[:count]
