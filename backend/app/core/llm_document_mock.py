"""严格基于文档片段的确定性要点抽取与 Mock 问答。"""
from __future__ import annotations

import re

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")


def extract_key_sentences(
    contexts: list[str],
    *,
    max_n: int,
    min_chars: int = 8,
) -> list[str]:
    """按原文顺序抽取、去重并限长文档要点，不引入外部内容。"""
    output: list[str] = []
    seen: set[str] = set()
    for context in contexts or []:
        for raw in _SENTENCE_SPLIT.split(context or ""):
            sentence = raw.strip()
            if len(sentence) < min_chars or sentence in seen:
                continue
            seen.add(sentence)
            output.append(sentence[:120])
            if len(output) >= max_n:
                return output
    return output


def answer_document_question(source_title: str, contexts: list[str], message: str) -> str:
    """用文档要点合成带引用的确定性回答；无要点时明确拒绝推测。"""
    question = (message or "").strip().replace("\n", " ")
    focus = (question[:40] + "…") if len(question) > 40 else (question or "这个问题")
    sentences = extract_key_sentences(contexts, max_n=4)
    if not sentences:
        return (
            f"关于「{focus}」，当前文档《{source_title}》中未提及相关内容。"
            "（本回答严格基于你上传的文档，不做文档之外的推测。）"
        )
    lead = f"根据你上传的《{source_title}》，就「{focus}」，文档中相关的内容如下："
    body = "\n".join(
        f"{index}. {sentence}[{index}]"
        for index, sentence in enumerate(sentences[:3], start=1)
    )
    tail = "以上要点均出自文档原文（见下方「来源」标注）。若需更系统的梳理，可在右侧生成讲义或图解。"
    return f"{lead}\n{body}\n{tail}"
