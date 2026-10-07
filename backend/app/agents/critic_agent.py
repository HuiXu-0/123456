"""内容审核校验 Agent（B5-a 骨架 / B5-b 真实接地口径）。

职责（需求文档 4.1）：对生成讲义逐句做 RAG 交叉校验，输出整体校验分与
幻觉率；评分低于阈值时工作流回 generator 重试（≤2 次），仍不过则降级。
prompt 模板 agentId=critic，必需占位符：draftContent / ragContext。

- mock：MockLLM 确定性输出；测试钩子 app.core.llm.set_force_critic_low(True)
  可强制低分以验证重试→降级路径；
- 真实模式（B5-b）：**不经 LLM**，按接口文档 15.3 口径本地计算——逐句与来源
  切片 embedding 相似度，低于阈值句占比 = hallucinationRate；
  validationScore = 接地句占比（1 - hallucinationRate）。prompt 仍现读模板渲染
  并记录（trace.promptExcerpt 与热更新验证不受影响）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents import render_template, run_agent
from app.agents.generator_agent import _format_rag_context
from app.core.llm import get_llm
from app.rag.grounding import sentence_grounding
from app.services.prompts import get_prompt

AGENT_ID = "critic"

# 通过线：与 workflows.learning_workflow.VALIDATION_THRESHOLD 同值
# （critic 不反向 import 工作流，避免循环依赖；改阈值需两处同步）
PASS_SCORE = 0.8
# issues 中列出的未接地句上限（重试反馈 prompt 不宜过长）
MAX_ISSUES = 5


def run_critic(
    db: Session,
    *,
    draft_content: str,
    rag_context: list[dict[str, Any]],
) -> dict[str, Any]:
    """执行审核。

    Returns:
        {agentId, name, prompt, output}，
        output 含 passed / validationScore / hallucinationRate / issues。
    """
    variables = {
        "draftContent": draft_content,
        "ragContext": _format_rag_context(rag_context),
    }
    if get_llm().is_mock:
        return run_agent(db, AGENT_ID, variables)

    # 真实模式：现读模板渲染（仅供 trace），校验本身走 15.3 本地接地计算
    prompt_row = get_prompt(db, AGENT_ID)
    if prompt_row is None:
        raise ValueError(f"未知 agentId：{AGENT_ID}")
    prompt = render_template(prompt_row["template"], variables)

    grounding = sentence_grounding(
        draft_content, [c.get("content", "") for c in rag_context]
    )
    rate = grounding["hallucinationRate"]
    score = round(1.0 - rate, 4)
    issues = [
        f"未接地句（与检索来源最大相似度低于阈值）：「{s}」"
        for s in grounding["ungroundedSentences"][:MAX_ISSUES]
    ]
    return {
        "agentId": AGENT_ID,
        "name": prompt_row["name"],  # 11.2 agents[].name（与模板表逐字一致）
        "prompt": prompt,
        "output": {
            "passed": score >= PASS_SCORE,
            "validationScore": score,
            "hallucinationRate": rate,
            "issues": issues,
        },
    }
