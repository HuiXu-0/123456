"""Agent Registry（R1：统一 Agent Framework）—— 全系统 Agent 的单一真值源。

## 为什么需要它

R1 之前系统里并存三套 Agent 形态：

1. 函数式（B5-a）：``diagnostic / generator / critic`` 走 ``run_agent()``；
2. 业务命名（无框架）：``planner / dialogue / feynman / evaluation`` 直调 ``LLMClient``；
3. OO 框架（TASK-002-B1/B2）：``BaseAgent`` + 4 个 Adapter，**只被测试引用**。

结果是"新增一个 Agent 要改 4 处"（文件 / service / endpoint / ``AGENT_ORDER`` 写死的
11.2 展示序），且没有任何一处能回答"系统里有哪些 Agent、各自会什么、拿哪个 prompt"。
本模块把三者**收敛为一份目录**：7 个业务 Agent 全部登记 ``capabilities`` / ``tools`` /
``prompt_id``，供 Supervisor 分发（R3）、trace 展示（R2）与能力内省使用。

## agent_id 与 adapter.agent_name 的区别（重要）

``agent_id`` 是**业务规范 id**：``PromptTemplate`` 主键、11.2 ``agents[].id``
（``AGENT_ORDER = ("diagnosis","generation","critic")``）、Prompt 归属都以它为准。

``BaseAgent.agent_name`` 是 **BaseAgent 框架内部名**，历史值与规范 id 并不一致
（``learning_diagnosis`` / ``resource_generation`` / ``quality_critic``），
已被 ``AgentMessage`` / 日志消费，**R1 不改**。两者在 ``manifest()`` 中分别暴露。

## 设计约束

- **只做目录，不做调度**：不执行 Agent、不编排流程（那是 R3 Supervisor 的职责）；
- **不复制业务逻辑**：``entrypoint`` 指向既有适配器类，逻辑仍在各 Agent 模块；
- **延迟播种**：``_build_catalog()`` 首次查询时才 import 各 Agent 模块，避免
  ``app.agents.__init__`` ↔ ``adapters`` 的循环导入（``adapters`` 会
  ``from app.agents import ...``）；
- **能力声明是硬约束**：7 个 Agent 的 ``capabilities`` 必须非空，否则分发层无从判断
  该调谁——这正是 R1 要解决的"Agent 不知道自己有什么能力"。

R1 阶段 ``tools`` 一律为空；**R4.2 起 `generation` / `critic` 已声明 Tool 能力**
（见 ``adapters.py``，最小权限原则）。注意：**声明 ≠ 执行** —— R4.2 只完成注册与
能力声明，**没有 Tool Loop**，不会因 LLM 输出自动调用（见 R4.2 报告 §10）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

logger = logging.getLogger("app.agents.registry")


@dataclass(frozen=True)
class AgentSpec:
    """一个 Agent 的注册项（不可变；修改请用 ``register(..., replace=True)``）。"""

    agent_id: str
    name: str = ""
    description: str = ""
    # 单一执行入口：BaseAgent 子类（需由调用方注入 db 等构造参数）。
    entrypoint: Callable[..., Any] | type | None = None
    input_schema: type[BaseModel] | None = None
    output_schema: type[BaseModel] | None = None
    # 能力声明 —— Supervisor 与前端能力展示的依据，禁止为空。
    capabilities: tuple[str, ...] = ()
    # 可调工具名（对应 agents.tools.ToolRegistry）。
    # R1 为空；R4.2 起 generation/critic 已声明 —— 仅能力声明，尚无 Tool Loop。
    tools: tuple[str, ...] = ()
    # Prompt 归属：services.prompts 的 agentId，或 LLMClient 侧 id。
    prompt_id: str | None = None
    version: str = "1.0"

    @property
    def has_entrypoint(self) -> bool:
        """是否存在可经 ``BaseAgent.run()`` 调用的单一入口。"""
        return self.entrypoint is not None

    def manifest(self) -> dict[str, Any]:
        """结构化能力清单（供 API / trace / Supervisor 内省）。"""
        entrypoint_name = getattr(self.entrypoint, "agent_name", None)
        return {
            "agentId": self.agent_id,
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "promptId": self.prompt_id,
            "hasEntrypoint": self.has_entrypoint,
            "entrypointName": entrypoint_name,
            "version": self.version,
        }


_registry: dict[str, AgentSpec] = {}
_seeded = False

# 7 个业务规范 agent_id（与 PromptTemplate / 11.2 agents[].id 对齐）。
BUSINESS_AGENT_IDS: tuple[str, ...] = (
    "diagnosis",
    "generation",
    "critic",
    "planner",
    "dialogue",
    "feynman",
    "evaluation",
)


def _validate(spec: AgentSpec) -> None:
    if not spec.agent_id or not spec.agent_id.strip():
        raise ValueError("agent_id must not be blank")
    if not spec.name or not spec.name.strip():
        raise ValueError(f"agent name must not be blank: {spec.agent_id!r}")
    if not isinstance(spec.capabilities, tuple) or not spec.capabilities:
        raise ValueError(f"agent must declare non-empty capabilities: {spec.agent_id!r}")
    for cap in spec.capabilities:
        if not isinstance(cap, str) or not cap.strip():
            raise ValueError(
                f"capabilities must be non-blank str: {spec.agent_id!r} → {cap!r}"
            )
    if not isinstance(spec.tools, tuple):
        raise ValueError(f"tools must be a tuple: {spec.agent_id!r}")
    for tool in spec.tools:
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(f"tools must be non-blank str: {spec.agent_id!r} → {tool!r}")


def _spec(adapter: type, *, agent_id: str, name: str, description: str) -> AgentSpec:
    """由 BaseAgent 子类反推 AgentSpec，避免能力声明在两处漂移。"""
    return AgentSpec(
        agent_id=agent_id,
        name=name,
        description=description,
        entrypoint=adapter,
        input_schema=adapter.input_schema,
        output_schema=adapter.output_schema,
        capabilities=adapter.capabilities,
        tools=adapter.tools,
        prompt_id=adapter.prompt_id,
    )


def _build_catalog() -> dict[str, AgentSpec]:
    """构造 7 个业务 Agent 的注册表（延迟 import，见模块文档）。"""
    from app.agents.adapters import (
        CriticAgentAdapter,
        DialogueAgentAdapter,
        EvaluationAgentAdapter,
        FeynmanAgentAdapter,
        LearningDiagnosisAgentAdapter,
        PathPlanningAgentAdapter,
        ResourceGenerationAgentAdapter,
    )

    entries = (
        _spec(
            LearningDiagnosisAgentAdapter,
            agent_id="diagnosis",
            name="学情诊断Agent",
            description="从学习者画像与掌握度定位薄弱知识点并给出优先级；不生成教学内容",
        ),
        _spec(
            ResourceGenerationAgentAdapter,
            agent_id="generation",
            name="领域知识生成Agent",
            description="依 RAG 检索上下文与画像生成讲义，禁止编造检索之外的事实",
        ),
        _spec(
            CriticAgentAdapter,
            agent_id="critic",
            name="内容审核校验Agent",
            description="对生成内容逐句做 RAG 接地校验，产出评分与幻觉率，驱动重试/降级",
        ),
        _spec(
            PathPlanningAgentAdapter,
            agent_id="planner",
            name="学习路径规划Agent",
            description="按画像/掌握度/岗位需求对知识树打分排序，产出有先修顺序的个性化路径",
        ),
        _spec(
            DialogueAgentAdapter,
            agent_id="dialogue",
            name="对话式学情诊断Agent",
            description="三段式画像采集（主观对话→诊断微测→偏好归类）的状态机单入口适配",
        ),
        _spec(
            FeynmanAgentAdapter,
            agent_id="feynman",
            name="费曼讲解评估Agent",
            description="评估学生用自己的话的讲解，定位知识缺口并给出引导追问",
        ),
        _spec(
            EvaluationAgentAdapter,
            agent_id="evaluation",
            name="学习过程评估Agent",
            description="汇总真实学习行为信号，产出多维评估、趋势与动态调整建议",
        ),
    )
    return {entry.agent_id: entry for entry in entries}


def _ensure_seeded() -> None:
    global _seeded
    if _seeded:
        return
    catalog = _build_catalog()
    for agent_id, spec in catalog.items():
        _registry.setdefault(agent_id, spec)
    _seeded = True
    logger.info("agent_registry_seeded count=%d ids=%s", len(catalog), sorted(catalog))


def register(spec: AgentSpec, *, replace: bool = False) -> AgentSpec:
    """注册一个 Agent。重复注册且未 ``replace`` → ValueError。"""
    _ensure_seeded()
    _validate(spec)
    key = spec.agent_id
    if key in _registry and not replace:
        raise ValueError(f"agent already registered: {key}")
    _registry[key] = spec
    logger.info(
        "agent_registered agent_id=%s capabilities=%d tools=%d",
        key,
        len(spec.capabilities),
        len(spec.tools),
    )
    return spec


def get(agent_id: str) -> AgentSpec:
    """按 id 取注册项。未注册 → KeyError。"""
    _ensure_seeded()
    try:
        return _registry[agent_id]
    except KeyError:
        raise KeyError(f"unknown agent: {agent_id}") from None


def available() -> tuple[str, ...]:
    """全部已注册 agent_id（字典序）。"""
    _ensure_seeded()
    return tuple(sorted(_registry))


def capabilities_of(agent_id: str) -> tuple[str, ...]:
    """某 Agent 的能力声明。未注册 → KeyError。"""
    return get(agent_id).capabilities


def tools_of(agent_id: str) -> tuple[str, ...]:
    """某 Agent 可调用的工具名（R1 一律为空）。未注册 → KeyError。"""
    return get(agent_id).tools


def specs() -> tuple[AgentSpec, ...]:
    """全部注册项（字典序）。"""
    _ensure_seeded()
    return tuple(_registry[key] for key in sorted(_registry))


def manifests() -> list[dict[str, Any]]:
    """全部能力清单（供 API / trace 消费）。"""
    return [spec.manifest() for spec in specs()]


def clear() -> None:
    """清空注册表（仅供测试；下一次查询会重新播种内置 7 个）。"""
    global _seeded
    _registry.clear()
    _seeded = False
