"""LangGraph 三类 Agent 的确定性 Mock 输出。"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

LectureBuilder = Callable[[str, str, str, str | None], str]

_force_critic_low = False


def diagnose(variables: dict[str, Any]) -> dict[str, Any]:
    """基于当前用户画像与掌握度，确定性定位薄弱知识点。"""
    target_kp = variables.get("kpId") or "attention"
    target_name = variables.get("kpName") or target_kp
    profile_summary = (variables.get("profileSummary") or "").strip()
    try:
        mastery_map = json.loads(variables.get("masteryStatus") or "{}")
        if not isinstance(mastery_map, dict):
            mastery_map = {}
    except (ValueError, TypeError):
        mastery_map = {}

    not_passed = [
        key for key, value in mastery_map.items() if value != "passed" and key != target_kp
    ]
    weak = [target_kp] + not_passed
    for fallback in ("transformer", "finetune"):
        if len(weak) >= 3:
            break
        if fallback not in weak:
            weak.append(fallback)
    weak = weak[:3]

    passed_n = sum(1 for value in mastery_map.values() if value == "passed")
    pending_n = sum(1 for value in mastery_map.values() if value != "passed")
    basis = profile_summary[:60] if profile_summary else "画像尚未采集（按通用基线）"
    reasoning = (
        f"依据该用户画像（{basis}）与掌握度（已通过 {passed_n} 项、待巩固 "
        f"{pending_n} 项）：「{target_name}」等 {len(weak)} 处为当前薄弱点。"
    )
    return {
        "weakKpIds": weak,
        "summary": f"检测到 {len(weak)} 处薄弱点，建议优先学习「{target_name}」",
        "reasoning": reasoning,
    }


def generate(variables: dict[str, Any], lecture_builder: LectureBuilder) -> dict[str, Any]:
    """使用调用方讲义构造器生成确定性 Agent 讲义。"""
    kp_name = variables.get("kpName") or "神经网络"
    difficulty = variables.get("difficulty") or "初级"
    tier = variables.get("depthTier") or None
    return {
        "markdown": lecture_builder(
            kp_name,
            difficulty,
            variables.get("description", ""),
            tier,
        )
    }


def review(variables: dict[str, Any], hallucination_rate: float) -> dict[str, Any]:
    """返回默认通过的审核结果；测试钩子开启时确定性返回低分。"""
    del variables  # 保留统一 Agent Mock 签名，当前审核结果不读取输入。
    if _force_critic_low:
        return {
            "passed": False,
            "validationScore": 0.42,
            "hallucinationRate": 0.18,
            "issues": ["第 2 段「梯度直觉」未在检索上下文中找到接地来源（测试钩子注入）"],
        }
    return {
        "passed": True,
        "validationScore": 0.93,
        "hallucinationRate": hallucination_rate,
        "issues": [],
    }


def set_force_critic_low(enabled: bool) -> None:
    """设置 Mock critic 低分测试钩子。"""
    global _force_critic_low
    _force_critic_low = bool(enabled)
