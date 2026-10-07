"""无 Provider 依赖的确定性评分基础函数。"""
from __future__ import annotations

import re
from typing import Any

_CREDIBILITY_HINTS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("arxiv", "nature", "acm", "ieee", "openreview"), 97),
    (("stanford", "cs231n", "cs224n", ".edu", "mit", "deeplearningbook", "harvard"), 95),
    (("pytorch", "tensorflow", "huggingface", "developers.google", "scikit-learn"), 93),
    (("coursera", "3blue1brown", "bilibili", "youtube", "jalammar"), 90),
)


def character_bigrams(text: str) -> set[str]:
    """返回去空白、忽略大小写的字符二元组集合。"""
    normalized = re.sub(r"\s+", "", (text or "").lower())
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def point_coverage(point: str, answer: str) -> float:
    """计算参考要点被作答覆盖的字符二元组召回率。"""
    point_bigrams = character_bigrams(point)
    if not point_bigrams:
        return 0.0
    return len(point_bigrams & character_bigrams(answer)) / len(point_bigrams)


def credibility_score(source: str, url: str) -> int:
    """按既有来源关键词启发式返回可信度分数。"""
    value = f"{source} {url}".lower()
    for keywords, score in _CREDIBILITY_HINTS:
        if any(keyword in value for keyword in keywords):
            return score
    return 82


def clamp_score(value: Any, default: int = 0) -> int:
    """把可转整数的输入截断到 0–100，非法输入返回默认值。"""
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default
