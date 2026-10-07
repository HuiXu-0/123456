"""领域知识生成 Agent（B5-a）。

职责（需求文档 4.1）：依据 RAG 检索上下文生成与学习者难度、能力画像适配的讲义，
禁止编造检索上下文之外的事实（能力锚定）。
prompt 模板 agentId=generation，必需占位符：kpName / description / difficulty /
ragContext / learnerProfile。
- learner_profile：当前用户画像文本（注入 {learnerProfile}，真实模式据此调节深度）；
- depth_tier：画像派生的能力档（advanced/beginner/basic），mock 据此产出深度不同的讲义。
审核未通过重试时，append 上一轮 critic 反馈（需求文档 4.2.2 _construct_feedback 思路）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents import run_agent

AGENT_ID = "generation"


def _format_rag_context(rag_context: list[dict[str, Any]]) -> str:
    """检索切片 → prompt 可读文本（带来源编号，供生成引用与审核对照）。"""
    if not rag_context:
        return "（无检索结果）"
    return "\n".join(
        f"[{i + 1}] {c.get('content', '')}" for i, c in enumerate(rag_context)
    )


def run_generator(
    db: Session,
    *,
    kp_name: str,
    difficulty: str,
    rag_context: list[dict[str, Any]],
    feedback: str | None = None,
    description: str = "",
    learner_profile: str = "",
    depth_tier: str | None = None,
) -> dict[str, Any]:
    """执行生成：现读模板 → 渲染（含 RAG 上下文、画像与重试反馈）→ LLM 产出讲义。

    Returns:
        {agentId, name, prompt, output}，output 含 markdown。
    """
    variables: dict[str, Any] = {
        "kpName": kp_name,
        "difficulty": difficulty,
        "ragContext": _format_rag_context(rag_context),
        "description": description,
        # 真实模式据此调节深度（注入模板 {learnerProfile}）；缺省给通用基线占位。
        "learnerProfile": learner_profile or "画像尚未采集（按通用基线）",
        # mock 据此产出深度不同的讲义（不参与模板渲染，仅供 _mock_generation 读取）。
        "depthTier": depth_tier,
    }
    result = run_agent(db, AGENT_ID, variables)
    if feedback:
        # 重试轮：把上一轮审核反馈追加到渲染后 prompt 末尾（模板无需新增占位符）
        result["prompt"] = f"{result['prompt']}\n\n[上一轮审核反馈，请修正]\n{feedback}"
    return result
