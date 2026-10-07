"""学情诊断 Agent（B5-a）。

职责（需求文档 4.1：DiagnosticAgent）：分析学习者画像与掌握度，定位薄弱
知识点并给出学习优先级；**不生成教学内容**（能力边界）。
prompt 模板 agentId=diagnosis，必需占位符：profileSummary / masteryStatus / targetJob。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents import run_agent

AGENT_ID = "diagnosis"


def run_diagnostic(
    db: Session,
    *,
    kp_id: str,
    kp_name: str = "",
    profile_summary: str = "六维能力画像：机器学习基础/神经网络较强，注意力机制/Transformer/大模型微调偏弱",
    mastery_status: str = "{}",
    target_job: str = "大模型应用工程师",
) -> dict[str, Any]:
    """执行诊断：现读模板 → 渲染 → MockLLM 返回薄弱点清单。

    Returns:
        {agentId, name, prompt, output}，output 含 weakKpIds/summary/reasoning。
    """
    return run_agent(
        db,
        AGENT_ID,
        {
            "profileSummary": profile_summary,
            "masteryStatus": mastery_status,
            "targetJob": target_job,
            # kpId/kpName 不属于模板占位符，仅供 mock 产出与目标知识点相关的确定性输出
            "kpId": kp_id,
            "kpName": kp_name or kp_id,
        },
    )
