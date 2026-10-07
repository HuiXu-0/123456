"""错题强化预置库、确定性 Mock 卡片与自洽审核清洗。"""
from __future__ import annotations

from typing import Any

from app.core.llm_output import extract_json
from app.core.llm_practice import audit_practice

# 错题强化预置库（接口文档 9.2，B6 mock）：nn 三题与前端 WeakPointReinforce.tsx
# 演示内容对齐（question_id 按种子题库 nn_q*，practice id 按契约「{qid}-r」）。
REINFORCE_BANK: dict[str, dict[str, Any]] = {
    "nn_q1": {
        "point": "神经元运算顺序",
        "recap": "记忆口诀：**先乘后加再激活** —— ① 输入×权重求和 → ② 加偏置 b → "
        "③ 激活函数。顺序不能颠倒，因为激活必须作用在「加权和+偏置」的结果上。",
        "practice": {
            "question_id": "nn_q1-r",
            "question_type": "single",
            "question_text": "【强化】若把激活函数放到加权求和之前，会发生什么？",
            "options": [
                {"option_id": "a", "option_text": "结果不变，顺序无所谓"},
                {"option_id": "b", "option_text": "失去对「加权和」整体的非线性变换，等价于线性模型"},
                {"option_id": "c", "option_text": "会让网络收敛更快"},
            ],
            "correct_answer": "b",
            "explanation": "激活必须作用于加权和+偏置的结果，提前激活会破坏非线性表达能力。",
        },
    },
    "nn_q2": {
        "point": "激活函数辨析",
        "recap": "激活函数 = 给神经元引入**非线性**的函数。常见三个：**ReLU**（max(0,x)）、"
        "**Sigmoid**、**Tanh**。注意「梯度 Gradient」是反向传播里的概念，**不是**激活函数。",
        "practice": {
            "question_id": "nn_q2-r",
            "question_type": "multiple",
            "question_text": "【强化】下列关于激活函数，正确的有？（多选）",
            "options": [
                {"option_id": "a", "option_text": "ReLU 在正区间梯度恒为 1"},
                {"option_id": "b", "option_text": "Sigmoid 输出范围是 (0,1)"},
                {"option_id": "c", "option_text": "Gradient 是一种激活函数"},
                {"option_id": "d", "option_text": "Tanh 输出零均值，范围 (-1,1)"},
            ],
            "correct_answer": ["a", "b", "d"],
            "explanation": "ReLU/Sigmoid/Tanh 描述均正确；Gradient（梯度）不是激活函数。",
        },
    },
    "nn_q3": {
        "point": "ReLU 与梯度消失",
        "recap": "**ReLU** 在正区间导数恒为 1，反向传播时梯度不会被反复压缩，因此能"
        "**缓解梯度消失**；而 Sigmoid/Tanh 在饱和区导数趋近 0，深层网络易梯度消失。",
        "practice": {
            "question_id": "nn_q3-r",
            "question_type": "boolean",
            "question_text": "【强化】Sigmoid 在深层网络中比 ReLU 更容易引起梯度消失。",
            "options": [
                {"option_id": "true", "option_text": "正确"},
                {"option_id": "false", "option_text": "错误"},
            ],
            "correct_answer": "true",
            "explanation": "Sigmoid 两端饱和、导数趋零，深层叠加后梯度迅速衰减，比 ReLU 更易梯度消失。",
        },
    },
}


def generate_mock_card(question: dict[str, Any]) -> dict[str, Any]:
    """命中预置库时返回精写卡片，否则生成确定性自洽变式。"""
    question_id = question["question_id"]
    bank = REINFORCE_BANK.get(question_id)
    if bank is not None:
        card = {"questionId": question_id, **bank}
        card["practice"] = {
            **bank["practice"],
            "question_id": f"{question_id}-r",
        }
        return card

    options = list(question.get("options") or [])
    rotated = options[1:] + options[:1] if len(options) > 1 else options
    point = str(question.get("question_text") or "").rstrip("？?。.")[:24]
    return {
        "questionId": question_id,
        "point": point,
        "recap": f"回顾：{question.get('explanation') or '请重读讲义对应小节。'}",
        "practice": {
            "question_id": f"{question_id}-r",
            "question_type": question["question_type"],
            "question_text": f"【强化·变式】{question['question_text']}",
            "options": rotated,
            "correct_answer": question["correct_answer"],
            "explanation": question.get("explanation") or "",
        },
    }


def clean_reinforcement(
    raw: str, wrong_questions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """解析、回正并审核真实模型生成的错题强化卡。"""
    data = extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return [], ["输出无法解析为契约 JSON（缺 items 数组）"]

    wrong_ids = [question["question_id"] for question in wrong_questions]
    cards: list[dict[str, Any]] = []
    issues: list[str] = []
    items = data["items"][: len(wrong_ids)]
    if len(items) < len(wrong_ids):
        issues.append(f"items 数量 {len(items)} 少于错题数 {len(wrong_ids)}")
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("practice"), dict):
            issues.append(f"items[{index}] 缺 practice 对象")
            continue
        question_id = item.get("questionId")
        if question_id not in wrong_ids:
            question_id = wrong_ids[index]
        practice = item["practice"]
        options = [
            {
                "option_id": str(option.get("option_id")),
                "option_text": str(option.get("option_text") or ""),
            }
            for option in (practice.get("options") or [])
            if isinstance(option, dict) and option.get("option_id")
        ]
        cleaned = {
            "question_id": str(practice.get("question_id") or f"{question_id}-r"),
            "question_type": practice.get("question_type"),
            "question_text": str(practice.get("question_text") or ""),
            "options": options,
            "correct_answer": practice.get("correct_answer"),
            "explanation": str(practice.get("explanation") or ""),
        }
        issues.extend(
            f"items[{index}]({question_id}) {message}"
            for message in audit_practice(cleaned)
        )
        cards.append(
            {
                "questionId": question_id,
                "point": str(item.get("point") or ""),
                "recap": str(item.get("recap") or ""),
                "practice": cleaned,
            }
        )
    return cards, issues
