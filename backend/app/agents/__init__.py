"""Agent 层（B5-a 约定 + R1 统一框架）。

三 Agent（diagnosis / generation / critic）共用约定：
- prompt 一律经 ``app.agents.prompts.PromptProvider`` **每次现读**
  （DB 优先：消费 B4-b 热更新，管理端 PUT 后下一次调用立即生效，禁止进程内缓存；
  DB 不可用时回落代码侧默认模板，见 ``app.agents.prompts.provider`` 文档）；
- 占位符按 ``{var}`` 直替（与 PromptTemplate.variables 契约一致），渲染后交
  ``app.core.llm.LLMClient.complete()`` 调用；
- 每个 run_* 返回统一结构 ``{agentId, name, prompt, output}``，供工作流记录
  trace（节点日志含 promptExcerpt，热更新可验证）。

## R1 变更（不改任何业务契约）

- ``render_template`` 的**实现**迁至 ``app.agents.prompts.provider``（canonical 位置），
  本模块继续 re-export —— ``from app.agents import render_template, run_agent`` 的既有
  调用方（``critic_agent`` 等）零改动；
- ``run_agent`` 改经 ``PromptProvider.resolve()`` 取模板。**渲染结果与改造前逐字一致**，
  未知 agentId 的 ``ValueError`` 消息保持 ``未知 agentId：{id}`` 不变；
- 唯一行为增强：DB 解析异常不再打断生成链路，而是记录 warning 并回落代码侧默认模板
  （这正是"Code Prompt 兜底"的目的）。

``app.agents.registry`` 负责全量 7 个 Agent 的注册与能力声明；本模块仍是三个模板型
Agent 的**执行入口**。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.prompts.provider import (
    PromptProvider,
    PromptResolution,
    get_prompt_provider,
    render_template,
)
from app.core.llm import get_llm

__all__ = [
    "PromptProvider",
    "PromptResolution",
    "get_prompt_provider",
    "render_template",
    "run_agent",
]


def run_agent(
    db: Session, agent_id: str, variables: dict[str, Any]
) -> dict[str, Any]:
    """统一执行入口：解析模板（DB 优先 / 代码兜底）→ 渲染 → LLMClient.complete。

    Args:
        db: 数据库会话（模板热更需要现读 DB）。
        agent_id: ``services.prompts`` 中登记的模板 id（diagnosis|generation|critic）。
        variables: 占位符变量，按 ``{var}`` 直替。

    Returns:
        ``{agentId, name, prompt, output}``（11.2 agents[] 与节点 trace 消费）。

    Raises:
        ValueError: ``agent_id`` 无可渲染模板（与改造前语义一致）。
    """
    resolved = get_prompt_provider().resolve(db, agent_id)
    if not resolved.available:
        raise ValueError(f"未知 agentId：{agent_id}")
    prompt = resolved.render(variables)
    output = get_llm().complete(prompt, agent_id=agent_id, variables=variables)
    return {
        "agentId": agent_id,
        "name": resolved.name,  # 11.2 agents[].name（与模板表逐字一致）
        "prompt": prompt,
        "output": output,
    }
