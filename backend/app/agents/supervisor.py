"""Supervisor Agent（R3）—— 统一决策入口，**不是第 8 个业务 Agent**。

## 定位

R3 要加的不是更多 Agent，而是一个**统一的决策入口**：

```
旧：Endpoint → 固定 workflow 调用 → resource_generation graph
新：User Request → Supervisor → Intent Routing → LangGraph 子图 → Agent 执行
```

Supervisor 只做三件事，**其余一律不碰**：

| 只负责 | 绝不负责 |
|---|---|
| 判断任务类型 | 业务生成 |
| 选择子图 | RAG 逻辑 |
| 传递 State | 评分逻辑 |
| | 学习算法 |

## 为什么它不进 `app.agents.registry`

`registry` 是**业务 Agent 的目录**（7 个，能力声明 + 执行入口）。Supervisor 没有业务
能力、没有 `input/output_schema`、不产出教学内容 —— 若把它注册进去，Registry 就会
变成第 8 个 Agent，**直接违反「不增加 Agent 数量」**。

正确的依赖方向是反过来的：**Registry 是 Supervisor 的输入**
（`registry.manifests()` → 可用能力 → 决定哪些子图可路由），
而不是 Supervisor 自己被登记成一个 Agent。

## 双层路由策略

- **L1 规则路由（默认，确定性）**：关键词 → 子图。**优先命中即返回，L2 根本不参与**；
- **L2 LLM 路由（默认关闭，`SUPERVISOR_LLM_ROUTING=false`）**：
  **仅在 L1 无规则命中、即将 fallback 时才被咨询**，且当前只留接线点（未接真实 LLM），
  返回 `None` 即回落 L1。

这一顺序是刻意的：**LLM 永远无法覆盖一条高置信的规则命中**，
从而结构性地保证「不让 LLM 替代规则系统」——不是靠约定，是靠调用顺序。

## 未知请求的 fallback

未知意图**不抛异常**，而是回落 `resource_generation`（唯一已完整实现的子图，
且与旧入口 `/workflow/execute` 的默认行为一致），通过 `reason` 前缀
`fallback:` 与较低的 `confidence`（≤ 0.5）与规则命中（0.9）区分。
"""
from __future__ import annotations

import logging
from typing import Any, Mapping
from pydantic import BaseModel, ConfigDict, Field

from app.agents import registry
from app.core.config import settings
from app.workflows.state import MultiAgentState

logger = logging.getLogger("app.agents.supervisor")

# ── 可路由子图（与 workflows/graphs/router.py 的条件边一一对应）────────────────
GRAPH_RESOURCE_GENERATION = "resource_generation"
GRAPH_PATH_PLANNING = "path_planning"
GRAPH_INTERACTION = "interaction"
GRAPH_EVALUATION = "evaluation"

TARGET_GRAPHS: tuple[str, ...] = (
    GRAPH_RESOURCE_GENERATION,
    GRAPH_PATH_PLANNING,
    GRAPH_INTERACTION,
    GRAPH_EVALUATION,
)
# 兜底目标：唯一已完整实现的子图，与旧入口默认行为一致
FALLBACK_GRAPH = GRAPH_RESOURCE_GENERATION

# 子图 → 所需能力（与 Registry 中已声明的 capabilities 取交集）。
# 只有「能力齐备」的子图才允许被路由到 —— 这是 Registry 参与决策的落点。
_GRAPH_REQUIREMENTS: dict[str, frozenset[str]] = {
    GRAPH_RESOURCE_GENERATION: frozenset({"rag_grounded_generation"}),
    GRAPH_PATH_PLANNING: frozenset({"path_planning"}),
    GRAPH_INTERACTION: frozenset({"subjective_extraction", "explanation_evaluation"}),
    GRAPH_EVALUATION: frozenset({"multi_dimension_metrics"}),
}

# L1 规则：(意图, 目标子图, 关键词)。**顺序即优先级** —— 具体意图在前、宽泛在后，
# 否则「生成学习路径」会被宽泛的「生成」抢先命中 resource_generation。
_INTENT_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "path_planning",
        GRAPH_PATH_PLANNING,
        ("学习路径", "学习计划", "路径规划", "学习顺序", "规划", "排程", "路线",
         "roadmap", "学习路线", "plan", "path"),
    ),
    (
        "evaluation",
        GRAPH_EVALUATION,
        ("评估", "评价", "测评", "检验", "成绩", "学得怎么样", "掌握情况",
         "assess", "evaluate", "assessment", "score"),
    ),
    (
        "interaction",
        GRAPH_INTERACTION,
        ("聊天", "对话", "辅导", "答疑", "提问", "费曼", "讲解", "画像", "诊断",
         "chat", "dialogue", "tutor", "feynman", "socratic"),
    ),
    (
        "resource_generation",
        GRAPH_RESOURCE_GENERATION,
        ("生成", "讲义", "资源", "图解", "视频", "思维导图", "导图", "测验", "刷题",
         "resource", "lecture", "generate", "video"),
    ),
)

# 置信度口径（固定值，便于测试断言与 trace 可读）
CONFIDENCE_RULE_HIT = 0.9   # L1 关键词命中
CONFIDENCE_SIGNAL_FALLBACK = 0.5  # 无关键词但有知识点 → 按默认资源生成
CONFIDENCE_NO_SIGNAL = 0.3       # 无任何信号


class RoutingDecision(BaseModel):
    """Supervisor 的输出契约（**结构固定，extra=forbid**）。"""

    model_config = ConfigDict(extra="forbid")

    target_graph: str = Field(description="目标子图 id，须属于 TARGET_GRAPHS")
    reason: str = Field(min_length=1, description="人类可读的路由理由")
    confidence: float = Field(ge=0.0, le=1.0, description="0.0–1.0 路由置信度")


class SupervisorAgent:
    """统一决策入口：输入 MultiAgentState，输出 RoutingDecision。

    不是 ``BaseAgent`` 子类、不进 Registry、不持有 DB Session、不调用业务函数。
    """

    def __init__(self, *, llm_routing: bool | None = None) -> None:
        # None = 读配置（默认 False）；显式传值供测试覆盖
        self.llm_routing = (
            settings.supervisor_llm_routing if llm_routing is None else bool(llm_routing)
        )

    # ── Registry 接入（R3 第三阶段）──────────────────────────────────────────

    def available_graphs(self) -> tuple[str, ...]:
        """当前可路由的子图（按 TARGET_GRAPHS 顺序）。

        由 ``registry.manifests()`` 的能力声明与 ``_GRAPH_REQUIREMENTS`` 取交集得出；
        **Registry 为空时回落到唯一兜底子图**，保证 Supervisor 永远有确定的输出。
        """
        declared: set[str] = set()
        for manifest in registry.manifests():
            declared.update(str(c) for c in (manifest.get("capabilities") or ()))

        available = tuple(
            graph
            for graph in TARGET_GRAPHS
            if declared & _GRAPH_REQUIREMENTS[graph]
        )
        if not available:
            logger.warning(
                "supervisor_no_capability graph fallback=%s declared=%d",
                FALLBACK_GRAPH,
                len(declared),
            )
            return (FALLBACK_GRAPH,)
        return available

    def capability_report(self) -> dict[str, Any]:
        """路由决策的可解释快照（供 trace / 排查用）。"""
        declared = {
            manifest["agentId"]: list(manifest.get("capabilities") or ())
            for manifest in registry.manifests()
        }
        return {
            "llmRouting": self.llm_routing,
            "availableGraphs": list(self.available_graphs()),
            "targets": list(TARGET_GRAPHS),
            "declaredAgents": declared,
        }

    # ── L1：确定性规则路由 ────────────────────────────────────────────────────

    @staticmethod
    def _context(state: Mapping[str, Any]) -> dict[str, Any]:
        return dict(state.get("learning_context") or {})

    def _rule_route(self, objective: str, available: tuple[str, ...]) -> RoutingDecision | None:
        """L1：关键词命中即返回；未命中返回 None（交给 L2 / fallback）。"""
        text = objective.strip().lower()
        if not text:
            return None
        for intent, graph, keywords in _INTENT_RULES:
            hit = next((k for k in keywords if k.lower() in text), None)
            if hit is None:
                continue
            if graph not in available:
                logger.info(
                    "supervisor_rule_skipped intent=%s graph=%s reason=graph_unavailable",
                    intent,
                    graph,
                )
                continue
            return RoutingDecision(
                target_graph=graph,
                reason=f"rule[{intent}] 命中关键词「{hit}」",
                confidence=CONFIDENCE_RULE_HIT,
            )
        return None

    def _fallback(self, *, has_kp: bool, detail: str) -> RoutingDecision:
        if has_kp:
            return RoutingDecision(
                target_graph=FALLBACK_GRAPH,
                reason=f"fallback: {detail}；存在知识点 kpId，按默认资源生成子图执行",
                confidence=CONFIDENCE_SIGNAL_FALLBACK,
            )
        return RoutingDecision(
            target_graph=FALLBACK_GRAPH,
            reason=f"fallback: {detail}；无可用信号，按默认资源生成子图执行",
            confidence=CONFIDENCE_NO_SIGNAL,
        )

    # ── L2：LLM 辅助路由（默认关闭，仅留接线点）──────────────────────────────

    def _llm_route(self, state: Mapping[str, Any]) -> RoutingDecision | None:
        """L2：LLM structured output 路由。

        **当前为接线点，未接真实 LLM** —— 即便被开启也返回 ``None`` 并回落 L1，
        保证「打开开关不会引入不可控行为」。未来接线时在此返回
        ``RoutingDecision``，调用方仍会做 `target_graph ∈ available` 校验。

        Args:
            state: 待路由的 MultiAgentState。

        Returns:
            RoutingDecision；当前恒为 None（未接线 / 校验失败）。
        """
        if not self.llm_routing:
            return None
        logger.warning(
            "supervisor_llm_routing_enabled_but_not_wired → 回落 L1 规则路由。"
            "接线点：app/agents/supervisor.py::_llm_route（需 LLM structured output "
            "且输出须通过 target_graph ∈ available_graphs() 校验）"
        )
        return None

    # ── 入口 ─────────────────────────────────────────────────────────────────

    def decide(self, state: MultiAgentState | Mapping[str, Any]) -> RoutingDecision:
        """判断任务类型并选定子图（不执行任何业务）。

        调用顺序（刻意如此，保证 LLM 不能替代规则）::

            L1 规则命中  ──是──▶ 直接返回（L2 不参与）
                  │否
                  ▼
            L2 LLM 路由  ──有──▶ 校验可用性后返回
                  │无 / 未接线
                  ▼
            fallback（resource_generation）
        """
        available = self.available_graphs()
        context = self._context(state)
        objective = str(context.get("objective") or "")
        has_kp = bool(str(context.get("kpId") or "").strip())

        # 1) L1 确定性规则（最高优先级）
        decision = self._rule_route(objective, available)
        if decision is not None:
            return decision

        # 2) L2 LLM 辅助 —— 仅在 L1 未命中时才被咨询
        decision = self._llm_route(state)
        if decision is not None and decision.target_graph in available:
            return decision
        if decision is not None:
            logger.warning(
                "supervisor_llm_decision_rejected target=%s available=%s",
                decision.target_graph,
                available,
            )

        # 3) fallback
        detail = (
            f"未识别到意图关键词（objective={objective!r}）" if objective.strip()
            else "objective 为空"
        )
        return self._fallback(has_kp=has_kp, detail=detail)

    def routing_trace(
        self, state: MultiAgentState | Mapping[str, Any], decision: RoutingDecision
    ) -> dict[str, Any]:
        """构造 R3.5 路由可观测性记录（5 字段，结构固定）。

        与 ``RoutingDecision`` 是两回事：后者是**决策本体**（3 字段、`extra=forbid`），
        本记录是**过程留痕**，额外带输入形态与是否兜底，供灰度期排查"这一单走了哪条路"。

        归属：存进 ``execution.routingTrace`` —— ``project_legacy_state`` **不投影它**，
        故 11.2 的 messages / trace / stats **完全不受影响**（R3.5 §5）。

        ``input_type`` 记录**输入形态**（是否拿到了意图自由文本）；
        决策来源（rule / llm / fallback）由 ``reason`` 的前缀携带
        （``rule[...]`` / ``llm[...]`` / ``fallback:``）。
        """
        objective = str((self._context(state).get("objective")) or "")
        return {
            "input_type": "objective" if objective.strip() else "empty",
            "selected_graph": decision.target_graph,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "fallback": decision.reason.startswith("fallback:"),
        }

    def __call__(self, state: MultiAgentState | Mapping[str, Any]) -> dict[str, Any]:
        """LangGraph 节点适配：把决策写回 State（只写 plan / execution.routingTrace）。

        **刻意不写 ``execution.currentNode``** —— 一旦写了 ``"supervisor"``，
        ``workflow_runner._phase_step_active`` 会把它当未知节点落到兜底分支
        返回 ``("complete", 4)``，使 ``stats.progress`` 立即变 100、前端显示
        「已完成」。不写 currentNode 则该帧被 ``on_update`` 跳过，
        **帧序列与旧入口完全一致**（R3.5 报告 §3 的灰度前提）。
        """
        decision = self.decide(state)
        trace = self.routing_trace(state, decision)
        logger.info(
            "supervisor_routing_trace input_type=%s selected_graph=%s "
            "confidence=%.2f fallback=%s reason=%s",
            trace["input_type"],
            trace["selected_graph"],
            trace["confidence"],
            trace["fallback"],
            trace["reason"],
        )
        return {
            "plan": {
                "targetGraph": decision.target_graph,
                "routingReason": decision.reason,
                "routingConfidence": decision.confidence,
            },
            "execution": {"routingTrace": trace},
        }
