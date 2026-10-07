"""费曼讲解评估 Agent（接口文档 18.2，C2）。

复用第 8 章苏格拉底辅导（8.7/15.4）的「引导式、不直接给答案」取向，但职责改为
**费曼学习法**：让学生用自己的话讲解 → 识别讲错/讲漏 → 定位知识缺口 gaps →
给出引导补讲的追问。评估经 LLMClient.feynman_eval（mock 确定性 / deepseek 真实，
契约清洗在 LLMClient 内）。

本 Agent 只负责编排一轮评估并回传结构化结果；gaps 的「应回看资源」review[]
（复用接口文档 6.3 resources[] 结构、指向真实资源端点）由服务层按 kp 注入——
Agent 不持有 db，保持与 dialogue_agent 同样的薄编排定位。
"""
from __future__ import annotations

from typing import Any

from app.core.llm import get_llm

AGENT_ID = "feynman"
AGENT_NAME = "费曼讲解评估Agent"


def respond(
    *,
    kp_id: str,
    kp_name: str,
    description: str,
    history: list[dict[str, str]],
    explanation: str,
) -> dict[str, Any]:
    """执行一轮费曼讲解评估。

    Args:
        kp_id / kp_name / description: 当前知识点（供评估锚定主题，gap.kpId 回正用）。
        history: 既往多轮 [{role, content}]（真实模式透传，支撑「补讲—再评」迭代）。
        explanation: 学生本轮讲解文本。

    Returns:
        {feedback, gaps, score, followups, complete}——gaps 元素为
        {kpId, title, detail, severity}（review[] 由服务层注入）；均已契约清洗。
    """
    return get_llm().feynman_eval(
        kp_id=kp_id,
        kp_name=kp_name,
        description=description,
        history=history,
        explanation=explanation,
    )
