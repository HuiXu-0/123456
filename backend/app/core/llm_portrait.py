"""画像维度契约与无 Provider 依赖的确定性 Mock 抽取。"""
from __future__ import annotations

from typing import Any

# 异质学生画像维度（接口文档 17.2），顺序即对话探查顺序。
PORTRAIT_DIMENSIONS: list[tuple[str, str]] = [
    ("knowledge_base", "知识基础"),
    ("prior_experience", "先验经验"),
    ("learning_goal", "学习目标"),
    ("cognitive_style", "认知风格"),
    ("learning_pace", "学习节奏"),
    ("error_preference", "易错点偏好"),
]
PORTRAIT_LABELS: dict[str, str] = dict(PORTRAIT_DIMENSIONS)
PORTRAIT_KEYS: tuple[str, ...] = tuple(key for key, _ in PORTRAIT_DIMENSIONS)
PORTRAIT_SOURCES: tuple[str, ...] = ("dialogue", "manual", "inferred", "diagnostic")
INFERRED_CONFIDENCE_CAP = 0.6

# 能力靠测、偏好归类型、主观靠对话；三类不可混用同一分值轴。
PORTRAIT_DIM_KINDS: dict[str, str] = {
    "knowledge_base": "ability",
    "prior_experience": "subjective",
    "learning_goal": "subjective",
    "cognitive_style": "preference",
    "learning_pace": "preference",
    "error_preference": "preference",
}
ABILITY_DIM_KEYS: tuple[str, ...] = tuple(
    key for key, kind in PORTRAIT_DIM_KINDS.items() if kind == "ability"
)
PREFERENCE_DIM_KEYS: tuple[str, ...] = tuple(
    key for key, kind in PORTRAIT_DIM_KINDS.items() if kind == "preference"
)
SUBJECTIVE_DIM_KEYS: tuple[str, ...] = tuple(
    key for key, kind in PORTRAIT_DIM_KINDS.items() if kind == "subjective"
)
PORTRAIT_KINDS: tuple[str, ...] = ("ability", "preference", "subjective")

PREFERENCE_QUESTIONS: dict[str, dict[str, Any]] = {
    "cognitive_style": {
        "prompt": "遇到一个全新的概念，你更想先看到哪一种？",
        "options": [
            {"optionKey": "visual", "label": "图像型", "hint": "先看一张示意图/结构图"},
            {"optionKey": "textual", "label": "文字型", "hint": "先读一段准确的定义"},
            {"optionKey": "example", "label": "案例型", "hint": "先看一个具体的例子"},
        ],
    },
    "learning_pace": {
        "prompt": "拿到一章新内容，你更倾向怎么推进？",
        "options": [
            {"optionKey": "overview", "label": "快速概览型", "hint": "先快速过一遍全局，再回头补细节"},
            {"optionKey": "deepdive", "label": "稳步细钻型", "hint": "从头逐点稳稳钻透再往下"},
        ],
    },
    "error_preference": {
        "prompt": "回想以往做错的题，最常见的原因是哪一类？",
        "options": [
            {"optionKey": "concept", "label": "概念混淆", "hint": "概念记混 / 理解有偏差"},
            {"optionKey": "calculation", "label": "计算粗心", "hint": "看错条件 / 算错一步"},
            {"optionKey": "coding", "label": "代码卡壳", "hint": "思路对但实现 / 代码写不对"},
        ],
    },
}
PREFERENCE_LABELS: dict[str, dict[str, str]] = {
    dimension: {option["optionKey"]: option["label"] for option in question["options"]}
    for dimension, question in PREFERENCE_QUESTIONS.items()
}


def sanitize_portrait_updates(updates: Any) -> list[dict[str, Any]]:
    """按固定维度、来源、类型和置信度规则清洗画像增量。"""
    if not isinstance(updates, list):
        return []
    by_key: dict[str, dict[str, Any]] = {}
    for item in updates:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if key not in PORTRAIT_KEYS:
            continue
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        source = item.get("source")
        if source not in PORTRAIT_SOURCES:
            source = "inferred"
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        if source == "inferred":
            confidence = min(confidence, INFERRED_CONFIDENCE_CAP)
        kind = PORTRAIT_DIM_KINDS.get(key, "subjective")
        cleaned: dict[str, Any] = {
            "key": key,
            "label": PORTRAIT_LABELS[key],
            "kind": kind,
            "value": value,
            "confidence": round(confidence, 2),
            "source": source,
        }
        score = item.get("score")
        if kind == "ability" and isinstance(score, (int, float)) and not isinstance(score, bool):
            cleaned["score"] = max(0, min(100, int(score)))
        basis = item.get("basis")
        if isinstance(basis, str) and basis.strip():
            cleaned["basis"] = basis.strip()
        option_key = item.get("optionKey")
        if kind == "preference" and isinstance(option_key, str) and option_key.strip():
            cleaned["optionKey"] = option_key.strip()
        by_key[key] = cleaned
    return list(by_key.values())


def extract_mock_portrait(
    message: str, context: dict[str, Any] | None, first_turn: bool
) -> list[dict[str, Any]]:
    """命中明确关键词时确定性抽取画像；无信号时不编造维度。"""
    text = message or ""
    low = text.lower()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(key: str, value: str, confidence: float, source: str, score: int | None = None) -> None:
        if key in seen:
            return
        seen.add(key)
        item: dict[str, Any] = {
            "key": key,
            "value": value,
            "confidence": confidence,
            "source": source,
        }
        if score is not None:
            item["score"] = score
        out.append(item)

    if "爬虫" in text:
        add("prior_experience", "有Python工程实践(爬虫)", 0.8, "dialogue")
    elif "python" in low and any(key in text for key in ("做过", "项目", "开发", "工程", "实践")):
        add("prior_experience", "有Python工程实践经验", 0.8, "dialogue")
    elif any(key in text for key in ("项目", "实践", "做过", "工作", "经验", "开发", "实习")):
        add("prior_experience", "有相关项目/工程实践经验", 0.75, "dialogue")

    if any(key in text for key in ("精通", "扎实", "很熟", "熟练")):
        add("knowledge_base", "扎实", 0.7, "dialogue", score=85)
    elif any(key in text for key in ("零基础", "没学过", "不会", "没接触", "薄弱", "刚入门", "不熟")):
        add("knowledge_base", "薄弱", 0.7, "dialogue", score=30)
    elif any(key in text for key in ("本科", "硕士", "博士", "学过", "了解", "科班", "计算机")):
        add("knowledge_base", "一般", 0.7, "dialogue", score=65)

    if any(key in text for key in ("转", "求职", "找工作", "岗位", "工程师", "职业", "入职", "面试")):
        add("learning_goal", "转大模型应用方向" if "大模型" in text else "职业转型/求职", 0.9, "dialogue")
    elif any(key in text for key in ("考试", "认证", "考研", "考证")):
        add("learning_goal", "考试/认证", 0.85, "dialogue")
    elif "兴趣" in text:
        add("learning_goal", "兴趣学习", 0.8, "dialogue")

    if any(key in text for key in ("动手", "实践", "代码", "做项目", "上手")) or "爬虫" in text:
        add("cognitive_style", "偏实践/动手型", 0.6, "dialogue")
    elif any(key in text for key in ("理论", "原理", "推导", "数学", "公式", "证明")):
        add("cognitive_style", "偏理论/推导型", 0.6, "dialogue")

    if any(key in text for key in ("时间紧", "快速", "突破", "尽快", "赶")):
        add("learning_pace", "偏快(集中突破)", 0.6, "dialogue")
    elif any(key in text for key in ("充裕", "稳", "扎实", "慢慢", "系统")):
        add("learning_pace", "稳扎稳打", 0.6, "dialogue")
    elif "适中" in text:
        add("learning_pace", "适中", 0.6, "dialogue")

    if any(key in text for key in ("概念", "混淆", "记不住")):
        add("error_preference", "概念易混淆", 0.5, "inferred")
    elif any(key in text for key in ("推导", "公式", "计算题")):
        add("error_preference", "计算/推导易错", 0.5, "inferred")
    elif any(key in text for key in ("代码", "实现", "编程", "调试", "报错")):
        add("error_preference", "代码实现易卡壳", 0.5, "inferred")

    if first_turn and context:
        goal = str(context.get("goal") or "").strip()
        if goal:
            add("learning_goal", goal, 0.9, "manual")
        major = str(context.get("major") or "").strip()
        if major:
            add("knowledge_base", f"{major}专业背景", 0.6, "manual", score=65)

    return sanitize_portrait_updates(out)
