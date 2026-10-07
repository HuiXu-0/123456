"""Router Graph（R3）—— ``START → Supervisor → 子图 → END``。

## 与旧入口的兼容策略（第四阶段）

```
旧（保持可用，一行未改）            新（R3 增量入口）
────────────────────────            ────────────────────────
run_learning_workflow()             run_router_workflow()
  └─ 直接跑 resource_generation       └─ supervisor 决策
                                       └─ conditional edge
                                          ├─ resource_generation（真实子图）
                                          ├─ path_planning      （未实现）
                                          ├─ interaction        （未实现）
                                          └─ evaluation         （未实现）
```

- **旧入口完全不受影响**：本模块只 `import` 旧模块的私有 helper
  （``_initial_state``），不反向被它引用 —— 依赖单向，无循环。
  这是本仓库既有做法：``services/resource.py`` 同样从 ``learning_workflow``
  导入私有名 ``_ability_tier``。
- **新入口是增量**：未接入任何 API（"不修改 API"），切换只需在
  ``services/workflow_runner._worker`` 里把调用换成 ``run_router_workflow``
  （签名兼容 + 多一个 ``objective`` 参数）。
- **Supervisor 子图不重复实现**：``resource_generation`` 分支直接调用
  R2 的 ``nodes.resource_generation.build_graph()``，**不复制任何业务逻辑**。

## 未实现子图的处理（诚实而非伪装）

``path_planning`` / ``interaction`` / ``evaluation`` 三个子图**尚未实现**。
本模块不为它们伪造节点输出，而是：

1. Router **仍设有分支节点**（满足"4 条分支"的结构，路由决策可观察）；
2. 节点只写 ``execution.status = "not_ready"``，**不执行任何业务、不伪造产物**；
3. ``run_router_workflow`` 检测到后抛 ``GraphNotReadyError``，
   携带 ``target_graph`` —— 既证明路由正确，也明确"此路未通"。

等到对应子图实现后，把 pending 节点换成真实子图即可，**路由与 Supervisor 无需改动**。

## Checkpoint

R3 **不接入** checkpoint（需求未要求，且能规避 R2 报告风险 1 的序列化问题）。
需要时沿用 ``app.core.checkpoint``，与旧入口同一开关。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.supervisor import (
    FALLBACK_GRAPH,
    TARGET_GRAPHS,
    GRAPH_EVALUATION,
    GRAPH_INTERACTION,
    GRAPH_PATH_PLANNING,
    GRAPH_RESOURCE_GENERATION,
    RoutingDecision,
    SupervisorAgent,
)
from app.core.trace import build_trace, project_legacy_state
from app.models.entities import KnowledgePoint, WorkflowTrace
from app.workflows.learning_workflow import MAX_RETRIES, Retriever, _initial_state, _mock_retriever
from app.workflows.nodes.resource_generation import build_graph as build_resource_generation
from app.workflows.state import MultiAgentState, WorkflowState

logger = logging.getLogger("app.workflows.graphs.router")


class GraphNotReadyError(RuntimeError):
    """路由到的子图尚未实现（R3 只有 resource_generation 是完整子图）。"""

    def __init__(self, *, target_graph: str, reason: str, confidence: float) -> None:
        self.target_graph = target_graph
        self.reason = reason
        self.confidence = confidence
        super().__init__(
            f"子图「{target_graph}」尚未实现（R3 仅 resource_generation 完整）：{reason}"
        )


def build_router_graph(
    db: Session,
    retriever: Retriever | None = None,
    disable_critic: bool = False,
    supervisor: SupervisorAgent | None = None,
    on_update: Callable[[WorkflowState], None] | None = None,
):
    """装配并编译路由图：``START → supervisor → {4 子图} → END``。

    Args:
        db: 数据库会话（**闭包注入 resource_generation 子图**，不进 State）。
        retriever: 检索 callable；同上闭包注入。
        disable_critic: 消融实验开关，仅透传给 resource_generation 子图。
        supervisor: 可注入的 Supervisor（测试用）；缺省按配置构造。

    Returns:
        编译后的 LangGraph 图。
    """
    sup = supervisor or SupervisorAgent()
    resource_graph = build_resource_generation(
        db, retriever or _mock_retriever, disable_critic=disable_critic
    )

    def supervisor_node(state: MultiAgentState) -> dict[str, Any]:
        # 命名内层函数（与 resource_generation 子图同款写法），避免某些 LangGraph
        # 版本对 lambda 节点的内省/序列化差异。
        return sup(state)

    def resource_generation_node(state: MultiAgentState) -> dict[str, Any]:
        # R4.5.2：**子图逐超步流式**，把每个子节点状态投影后交给 on_update。
        #
        # 若用 invoke()（一次拿终态），父图只会为本节点产出 **一帧**，
        # 子图内部的 diagnostic/retrieval/generation/validation 全部不可见 →
        # 11.2 渐进快照（phase/step 分级点亮）会塌缩成单帧 `decision`，
        # 前端大屏进度展示失效。这是生产行为缺陷，不能靠改测试掩盖。
        #
        # 子图返回的是**它自己的完整终态**；此时父图只跑过 supervisor（不写
        # messages/errors/nodeLog），故三个列表字段在父侧为空，
        # 经 reducer 合并后不会重复追加（见 tests/test_supervisor_fallback.py）。
        final_state: dict[str, Any] = {}
        for child_state in resource_graph.stream(dict(state), stream_mode="values"):
            final_state = child_state
            if on_update is None:
                continue
            if not (child_state.get("execution") or {}).get("currentNode"):
                continue
            on_update(project_legacy_state(child_state))
        return final_state

    def _pending_node(graph_name: str) -> Callable[[MultiAgentState], dict[str, Any]]:
        def _node(state: MultiAgentState) -> dict[str, Any]:
            logger.info("router_pending target=%s（子图未实现，不执行业务）", graph_name)
            # ⚠️ 只写 status、**不写 currentNode**：未知节点名会让
            # workflow_runner._phase_step_active 落到兜底 ("complete", 4)，
            # 使 stats.progress 提前置 100（R3.5 报告 §3 阻断项）。
            # run_router_workflow 判定未实现用的是 status，与 currentNode 无关。
            return {"execution": {"status": "not_ready"}}

        _node.__name__ = f"pending_{graph_name}"
        return _node

    def _route(state: MultiAgentState) -> str:
        """条件边：读 Supervisor 写入的 plan.targetGraph（**不重复调用 Supervisor**）。"""
        target = str((state.get("plan") or {}).get("targetGraph") or "")
        if target not in TARGET_GRAPHS:
            logger.warning("router_unknown_target=%r → %s", target, FALLBACK_GRAPH)
            return FALLBACK_GRAPH
        return target

    router = StateGraph(MultiAgentState)
    router.add_node("supervisor", supervisor_node)
    router.add_node(GRAPH_RESOURCE_GENERATION, resource_generation_node)
    router.add_node(GRAPH_PATH_PLANNING, _pending_node(GRAPH_PATH_PLANNING))
    router.add_node(GRAPH_INTERACTION, _pending_node(GRAPH_INTERACTION))
    router.add_node(GRAPH_EVALUATION, _pending_node(GRAPH_EVALUATION))

    router.add_edge(START, "supervisor")
    router.add_conditional_edges(
        "supervisor",
        _route,
        {graph: graph for graph in TARGET_GRAPHS},
    )
    for graph in TARGET_GRAPHS:
        router.add_edge(graph, END)
    return router.compile()


def run_router_workflow(
    db: Session,
    *,
    user_id: str,
    kp_id: str,
    difficulty: str = "初级",
    target_job: str = "大模型应用工程师",
    objective: str = "",
    retriever: Retriever | None = None,
    workflow_id: str | None = None,
    on_update: Callable[[WorkflowState], None] | None = None,
    max_retries: int = MAX_RETRIES,
    disable_critic: bool = False,
) -> dict[str, Any]:
    """经 Supervisor 路由执行（R3 新增入口）。

    签名与 ``run_learning_workflow`` **兼容**（多一个 ``objective``，
    用于 Supervisor 的意图识别），返回结构同样是
    ``{workflowId, finalOutput, trace}`` 并**额外**携带 ``routing``，
    因此将来在 ``workflow_runner._worker`` 里一行切换即可。

    Args:
        objective: 用户这次想干什么（自然语言），Supervisor 的 L1 规则路由输入。
            留空 → 走 fallback（与旧入口默认行为一致）。

    Returns:
        ``{workflowId, finalOutput, trace, routing, routingTrace}``。

        其中 ``trace`` 是 11.2 结构（**逐字不变**）；``routing`` /
        ``routingTrace`` 是 R3.5 新增的**进程内**键，接口层不透出、不落库。

    Raises:
        GraphNotReadyError: 路由到尚未实现的子图（path_planning / interaction /
            evaluation）；``target_graph`` 字段标明路由结果。
    """
    workflow_id = workflow_id or f"wf_{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc)
    kp = db.get(KnowledgePoint, kp_id)
    kp_name = kp.name if kp is not None else kp_id
    kp_description = (kp.description or "") if kp is not None else ""

    router_graph = build_router_graph(
        db,
        retriever=retriever,
        disable_critic=disable_critic,
        on_update=on_update,  # R4.5.2：帧由子图逐节点发出
    )
    initial_state = _initial_state(
        user_id=user_id,
        workflow_id=workflow_id,
        kp_id=kp_id,
        kp_name=kp_name,
        kp_description=kp_description,
        difficulty=difficulty,
        target_job=target_job,
        max_retries=max_retries,
    )
    # 路由输入：旧入口不写 objective，新入口按调用方给的自由文本
    initial_state["learning_context"]["objective"] = objective

    # 与旧入口同款：无 checkpointer → config 仅 recursion_limit
    config: dict[str, Any] = {"recursion_limit": 50}

    # R4.5.2：**父图不再发帧** —— 帧已由 resource_generation 子图逐超步发出
    #（见 build_router_graph）。若此处再发一次，decision 会出现重复帧。
    # pending 分支的节点不写 currentNode，本就不会发帧（与修复前一致）。
    final_state: MultiAgentState | None = None
    for state in router_graph.stream(initial_state, config=config, stream_mode="values"):
        final_state = state

    if final_state is None:
        raise RuntimeError("路由图未产出状态")

    execution = final_state.get("execution") or {}
    plan = final_state.get("plan") or {}
    routing = RoutingDecision(
        target_graph=str(plan.get("targetGraph") or FALLBACK_GRAPH),
        reason=str(plan.get("routingReason") or "未记录路由理由"),
        confidence=float(plan.get("routingConfidence") or 0.0),
    ).model_dump()
    # R3.5 路由可观测性：只在进程内返回，**不进 11.2 trace**（project_legacy_state 不投影它）
    routing_trace = dict(execution.get("routingTrace") or {})

    if execution.get("status") == "not_ready":
        raise GraphNotReadyError(**routing)

    if not (final_state.get("result") or {}):
        raise RuntimeError("工作流未产出 final_output")

    final_output = dict(final_state.get("result") or {})
    degraded = bool(final_output.get("degraded"))
    node_log = list(execution.get("nodeLog") or [])
    rag_context_used = list((final_state.get("evidence") or {}).get("ragContext") or [])

    trace = build_trace(
        workflow_id=workflow_id,
        messages=final_state.get("messages") or [],
        node_log=node_log,
        rag_context=rag_context_used,
        degraded=degraded,
    )
    status = "degraded" if degraded else "success"

    row = WorkflowTrace(
        id=workflow_id,
        user_id=user_id,
        kp_id=kp_id,
        status=status,
        degraded=degraded,
        validation_score=final_output.get("validationScore"),
        hallucination_rate=final_output.get("hallucinationRate"),
        retry_count=final_output.get("retryCount", 0),
        agents=trace["agents"],
        messages=trace["messages"],
        node_log=trace["nodes"],
        rag_context_used=trace["ragContextUsed"],
        started_at=started_at,
        ended_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

    return {
        "workflowId": workflow_id,
        "finalOutput": final_output,
        "trace": trace,
        "routing": routing,
        "routingTrace": routing_trace,
    }
