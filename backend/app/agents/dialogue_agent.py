"""对话式学情诊断 Agent（接口文档 17.1；C2 三段式重构）。

职责（赛题功能 1）：通过引导式对话 + 行为微测自动构建异质学生动态画像
（StudentPortrait，17.2）。**不再纯自陈**——把诊断拆成三段，各用各的测法：

  ① 对话开场（subjective）：自然语言采集「学习目标 / 先验经验」等主观信息；
  ② 诊断微测（ability，核心）：有选择性地抛出由浅入深的客观题（复用 quiz 题库，
     见 services.diagnostic_microtest），用「答对/答错」行为反推真实能力，学生说
     「我懂」就出题验证——能力靠测不靠说；
  ③ 偏好选择（preference）：几道「你更喜欢哪种」二/三选一，从选择归类认知风格 /
     学习节奏 / 易错倾向，**只归类型、不打分、不上 0-100 轴**。

本 Agent 提供各段的「问什么 / 如何归类 / 何时推进」纯逻辑；会话状态与 DB（微测取题/
判分/写基线）由 services.profile_dialogue 编排（它持有会话与 SQLAlchemy 会话）。

防幻觉（17.1）：维度抽取的契约清洗在 LLMClient/_sanitize_portrait_updates 内完成；
能力维只由微测产出（带依据 basis），主观维不强行打分，偏好维严禁打分。
"""
from __future__ import annotations

from typing import Any

from app.core.llm import (
    PREFERENCE_DIM_KEYS,
    PREFERENCE_QUESTIONS,
    SUBJECTIVE_DIM_KEYS,
    _PORTRAIT_LABELS,
    _PREFERENCE_LABELS,
    get_llm,
)

AGENT_ID = "dialogue"
AGENT_NAME = "对话式学情诊断Agent"

# 诊断三段（+ 收尾）
STAGE_OPENING = "opening"
STAGE_MICROTEST = "microtest"
STAGE_PREFERENCE = "preference"
STAGE_DONE = "done"

# 偏好段探查顺序（= 偏好维顺序）
PREFERENCE_ORDER: list[str] = list(PREFERENCE_DIM_KEYS)

# ── 开场段（subjective）──────────────────────────────────────────────────────
# 主观维探查顺序：先先验经验、再学习目标（首轮用户自述背景多落经验，二轮问目标）。
_OPENING_PROBE: list[str] = ["prior_experience", "learning_goal"]
_OPENING_QUESTION: dict[str, str] = {
    "prior_experience": "先随便聊聊——相关的课程、项目或实践你接触过哪些？",
    "learning_goal": "明白了。那这次学习你最想达成的目标是什么？",
}
_OPENING_CHIPS: dict[str, list[str]] = {
    "prior_experience": ["做过相关项目", "只在课程里学过", "基本没接触"],
    "learning_goal": ["转岗 / 求职", "考试 / 认证", "项目需要", "兴趣自学"],
}
_MICROTEST_INTRO = (
    "好的，主观情况我了解了。下面做几道小题快速「测」一下你的真实基础——"
    "凭直觉选就行，答得准我才能把能力画像测准，而不是只听你说。"
)
_PREFERENCE_INTRO = "速测完成 ✦，能力画像已据作答测出。最后了解一下你的学习偏好（只归类型、不打分）——"
_CLOSING_REPLY = (
    "都记下了 🎉 能力靠测、偏好归类型、主观靠对话，你的动态学习画像已经构建完整，"
    "接下来就可以据此生成个性化学习路径了。"
)


def opening_first_question() -> str:
    return _OPENING_QUESTION["prior_experience"]


# ── 开场段：抽取主观维（仅 subjective，能力/偏好不在此自陈）────────────────────

def extract_subjective(
    *,
    message: str,
    context: dict[str, Any] | None,
    first_turn: bool,
    target_key: str,
) -> list[dict[str, Any]]:
    """开场段抽取主观维度增量（只取 learning_goal / prior_experience）。

    经 LLMClient.extract_portrait 抽取后**仅保留主观维**——能力维严禁在此自陈打分，
    偏好维改由选择题归类。若本轮目标主观维未抽到，则从原话兜底合成（保证该维有值、
    诊断可推进到 6 维）；原话无实质内容时给低置信占位（不臆造）。
    """
    raw = get_llm().extract_portrait(
        message=message, context=context if first_turn else None, first_turn=first_turn
    )
    subjective = [d for d in raw if d["key"] in SUBJECTIVE_DIM_KEYS]
    # 能力维若被自陈带出 → 丢弃 score、降级为不打分主观备注（能力只认微测）
    have = {d["key"] for d in subjective}
    if target_key not in have:
        subjective.append(_synthesize_subjective(target_key, message))
    return subjective


def _synthesize_subjective(key: str, message: str) -> dict[str, Any]:
    text = (message or "").strip()
    substantive = len(text) >= 4 and text not in ("你好", "嗨", "hi", "在吗", "嗯")
    if substantive:
        value = text if len(text) <= 24 else text[:24] + "…"
        return {"key": key, "value": value, "confidence": 0.55, "source": "dialogue"}
    placeholder = "暂无明确描述" if key == "prior_experience" else "目标待明确"
    return {"key": key, "value": placeholder, "confidence": 0.4, "source": "inferred"}


def opening_reply(ack: bool, next_key: str | None) -> tuple[str, list[str]]:
    """开场段回复：简短确认 + 下一主观维追问；next_key=None 表示开场结束（转微测）。"""
    head = "了解了。" if ack else "好的。"
    if next_key is None:
        return head + _MICROTEST_INTRO, []
    return head + _OPENING_QUESTION[next_key], list(_OPENING_CHIPS[next_key])


def next_opening_key(collected: set[str]) -> str | None:
    """开场下一个要问的主观维；都齐 → None（转微测）。"""
    for key in _OPENING_PROBE:
        if key not in collected:
            return key
    return None


# ── 微测段（ability）：交互对象 + 回复 ────────────────────────────────────────

def quiz_interaction(session_id: str, idx: int, question: dict[str, Any]) -> dict[str, Any]:
    """把一道微测题封装成前端可渲染的交互对象（引导式抛题）。"""
    options = [
        {"value": o.get("option_id"), "label": o.get("option_text", "")}
        for o in question.get("options", [])
    ]
    return {
        "id": f"{session_id}:microtest:{idx}",
        "type": "quiz",
        "dimKey": "knowledge_base",
        "prompt": question["questionText"],
        "options": options,
        "meta": {"kpId": question["kpId"], "kpName": question["kpName"]},
    }


def microtest_reply(ack_correct: bool | None, question: dict[str, Any]) -> str:
    """微测段回复：对上一题的中性确认（不泄题对错以免诱导）+ 抛出下一题题面。"""
    if ack_correct is None:
        ack = ""  # 首题（开场刚转入）由 intro 衔接，无需 ack
    else:
        ack = "好，记下了。"
    return f"{ack}下面这道关于「{question['kpName']}」的题，你的判断是？\n{question['questionText']}"


# ── 偏好段（preference）：交互对象 + 归类 + 回复 ──────────────────────────────

def preference_interaction(session_id: str, idx: int, dim_key: str) -> dict[str, Any]:
    """把一个偏好维封装成「二/三选一」交互对象（只归类型、不打分）。"""
    q = PREFERENCE_QUESTIONS[dim_key]
    options = [
        {"value": o["optionKey"], "label": o["label"], "hint": o["hint"]}
        for o in q["options"]
    ]
    return {
        "id": f"{session_id}:preference:{idx}",
        "type": "preference",
        "dimKey": dim_key,
        "prompt": q["prompt"],
        "options": options,
    }


def preference_reply(first: bool, dim_key: str) -> str:
    """偏好段回复：引导语 + 偏好问题。first=True 用 intro 衔接微测收尾。"""
    head = _PREFERENCE_INTRO if first else "好的。"
    return head + PREFERENCE_QUESTIONS[dim_key]["prompt"]


def classify_preference(dim_key: str, value: str) -> dict[str, Any]:
    """据用户选择/作答把偏好维归类到某个类型（optionKey），**不打分**。

    优先按 optionKey 精确命中（前端点选回传 optionKey）；否则对自由文本做关键词归类；
    都不命中 → 取首个类型并标低置信（不臆造强偏好）。返回未清洗的偏好维增量。
    """
    options = PREFERENCE_QUESTIONS[dim_key]["options"]
    value_norm = (value or "").strip()
    option_key = None
    confidence = 0.9
    # 1) optionKey 精确命中
    for o in options:
        if value_norm == o["optionKey"]:
            option_key = o["optionKey"]
            break
    # 2) 自由文本关键词归类
    if option_key is None:
        option_key = _match_preference_text(dim_key, value_norm)
        confidence = 0.7
    # 3) 兜底：首个类型，低置信
    if option_key is None:
        option_key = options[0]["optionKey"]
        confidence = 0.4
    label = _PREFERENCE_LABELS[dim_key][option_key]
    return {
        "key": dim_key,
        "value": label,
        "optionKey": option_key,
        "confidence": confidence,
        "source": "dialogue",
    }


# 偏好自由文本归类关键词（兜底，前端点选优先走 optionKey 精确命中）
_PREFERENCE_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "cognitive_style": {
        "visual": ("图", "示意", "图像", "可视", "画"),
        "textual": ("定义", "文字", "概念", "读", "原理"),
        "example": ("例", "案例", "实例", "上手", "实践", "动手"),
    },
    "learning_pace": {
        "overview": ("概览", "全局", "快速", "先过", "通读", "快"),
        "deepdive": ("细", "钻", "稳", "逐", "深入", "扎实"),
    },
    "error_preference": {
        "concept": ("概念", "混淆", "记混", "理解"),
        "calculation": ("计算", "粗心", "算错", "看错", "推导"),
        "coding": ("代码", "实现", "编程", "调试", "写不对"),
    },
}


def _match_preference_text(dim_key: str, text: str) -> str | None:
    for option_key, words in _PREFERENCE_KEYWORDS.get(dim_key, {}).items():
        if any(w in text for w in words):
            return option_key
    return None


def closing_reply() -> str:
    return _CLOSING_REPLY
