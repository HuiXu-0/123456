"""练习题契约与答案自洽性审核。

该模块只验证结构化练习题，不调用模型，也不负责生成或评分学生答案。
"""
from __future__ import annotations

from typing import Any

_OBJECTIVE_QUESTION_TYPES: tuple[str, ...] = ("single", "multiple", "boolean")


def audit_practice(practice: dict[str, Any]) -> list[str]:
    """审核 QuizQuestion 结构和正确答案自洽性，返回稳定顺序的问题清单。"""
    issues: list[str] = []
    question_type = practice.get("question_type")
    if question_type not in _OBJECTIVE_QUESTION_TYPES:
        issues.append(f"question_type 非法：{question_type}")
    if not str(practice.get("question_text") or "").strip():
        issues.append("question_text 为空")

    option_ids = [option.get("option_id") for option in (practice.get("options") or [])]
    if len(option_ids) < 2:
        issues.append("options 少于 2 个")
    if len(set(option_ids)) != len(option_ids):
        issues.append("option_id 重复")

    correct = practice.get("correct_answer")
    if isinstance(correct, list):
        if question_type != "multiple":
            issues.append("correct_answer 为数组但题型不是 multiple")
        if not correct or not all(answer in option_ids for answer in correct):
            issues.append(f"correct_answer {correct} 未全部出现在 options 中")
    elif correct not in option_ids:
        issues.append(f"correct_answer {correct!r} 不在 options 中")

    if not str(practice.get("explanation") or "").strip():
        issues.append("explanation 为空")
    return issues
