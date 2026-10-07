"""学习资源生成工作流（B5-a 起建立；R2 迁移到统一 LangGraph Runtime）。

## R2 变更：换内核，不换契约

```
R2 之前                                     R2 之后
────────────                                ────────────
WorkflowState（扁平、混装）          →      MultiAgentState（分层 10 字段）
手工 _msg() 拼 11.2 字符串           →      AgentMessage 执行流水
节点闭包直接写扁平键                 →      nodes/resource_generation 包装业务函数
无 checkpoint                        →      core/checkpoint（默认关闭）
```

**未变**：``run_learning_workflow()`` 的签名与返回结构、11.2 trace 结构与文案、
``AGENT_ORDER`` / ``WorkflowState`` / ``Retriever`` / ``MAX_RETRIES`` /
``VALIDATION_THRESHOLD`` / ``_ability_tier`` 的导入路径、拓扑与重试/降级语义、
``WorkflowTrace`` 落库字段。

三条依赖方向（避免循环导入）：

```
learning_workflow ──▶ nodes.resource_generation ──▶ agents/* / services/* / core.trace
                         └─ 定义 VALIDATION_THRESHOLD / MAX_RETRIES / Retriever /
                            _ability_tier（本模块 re-export）
core.trace ──▶ workflows.state（仅类型与投影，不回指本模块）
```

## 依赖方（签名冻结的依据）

| 调用方 | 用到的符号 |
|---|---|
| ``services/workflow_runner.py`` | ``AGENT_ORDER`` ``Retriever`` ``WorkflowState`` ``run_learning_workflow`` |
| ``services/resource.py`` | ``_ability_tier`` ``run_learning_workflow`` |
| ``scripts/regen_lectures.py`` | ``_ability_tier`` |
| ``scripts/metrics/ablation.py`` | ``run_learning_workflow`` |
| ``tests/test_workflow_b5a.py`` | ``MAX_RETRIES`` ``VALIDATION_THRESHOLD`` ``run_learning_workflow`` |
| ``tests/test_cfix_logic.py`` | ``run_learning_workflow`` |
| ``agents/critic_agent.py``（注释引用） | ``VALIDATION_THRESHOLD`` |

## 拓扑（与迁移前逐字一致）

```
diagnostic → retrieval → generation → validation ─┬→ decision → END
                                          ▲        │
                                          └── retry ≤2 ──┘（评分 < 阈值）
仍不过 → 降级输出：final_output 标记 validation_score 与 degraded:true。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core import checkpoint
from app.core.trace import (
    AGENT_ORDER,
    PROMPT_EXCERPT_LEN,
    build_trace,
    project_legacy_state,
)
from app.models.entities import KnowledgePoint, WorkflowTrace
from app.workflows.nodes.resource_generation import (
    MAX_RETRIES,
    VALIDATION_THRESHOLD,
    Retriever,
    _ability_tier,  # noqa: F401 — 兼容再导出：services/resource、scripts/regen_lectures 依赖本路径
    _mock_retriever,
    build_graph,
)
from app.workflows.state import (
    LegacyWorkflowState,
    MultiAgentState,
    WorkflowState,
)

__all__ = [
    "AGENT_ORDER",
    "MAX_RETRIES",
    "PROMPT_EXCERPT_LEN",
    "Retriever",
    "VALIDATION_THRESHOLD",
    "WorkflowState",
    "LegacyWorkflowState",
    "MultiAgentState",
    "run_learning_workflow",
]


def _build_graph(
    db: Session,
    retriever: Retriever,
    disable_critic: bool = False,
    checkpointer: Any = None,
):
    """装配并编译资源生成子图（实现见 app.workflows.nodes.resource_generation）。"""
    return build_graph(
        db, retriever, disable_critic=disable_critic, checkpointer=checkpointer
    )


def _initial_state(
    *,
    user_id: str,
    workflow_id: str,
    kp_id: str,
    kp_name: str,
    kp_description: str,
    difficulty: str,
    target_job: str,
    max_retries: int,
) -> MultiAgentState:
    """构造初始 MultiAgentState。

    **所有带 reducer 的字段必须显式给初值**：messages/errors 给 ``[]``、
    nodeLog 给 ``[]``，否则首帧投影会读到缺键（与迁移前显式初始化
    ``messages`` / ``error_details`` / ``node_execution_log`` 的做法一致）。
    """
    return {
        "identity": {"userId": user_id, "workflowId": workflow_id, "sessionId": None},
        "learning_context": {
            "kpId": kp_id,
            "kpName": kp_name,
            "kpDescription": kp_description,
            "difficulty": difficulty,
            "targetJob": target_job,
        },
        "plan": {"maxRetries": max_retries},
        "execution": {
            "currentNode": "",
            "status": "pending",
            "retryCount": 0,
            "nodeLog": [],
        },
        "evidence": {"ragContext": [], "references": []},
        "messages": [],
        "artifacts": {"markdown": "", "feedback": None},
        "quality": {"result": {}, "hallucinationRate": None},
        "errors": [],
        "result": {},
    }


def run_learning_workflow(
    db: Session,
    *,
    user_id: str,
    kp_id: str,
    difficulty: str = "初级",
    target_job: str = "大模型应用工程师",
    retriever: Retriever | None = None,
    workflow_id: str | None = None,
    on_update: Callable[[WorkflowState], None] | None = None,
    max_retries: int = MAX_RETRIES,
    disable_critic: bool = False,
) -> dict[str, Any]:
    """执行完整工作流并持久化 trace。

    **签名与返回结构是冻结契约**（5 类调用方，见模块文档），R2 只换内部实现：
    图状态改为 ``MultiAgentState``，消息改为 ``AgentMessage`` 流水，
    ``on_update`` 与最终 trace 均经 ``app.core.trace`` 投影回 11.2 结构。

    B11 消融实验参数（**仅实验脚本可用，业务接口恒用默认值**，不进生产配置）：
    - retriever 传空检索器 → 去 RAG；disable_critic=True → 去审核闭环（首版直接交付）；
    - max_retries=0 → 去重试（低分即降级，不再回炉）。

    B7-a 增补（拓扑 / trace 结构不变）：
    - workflow_id 可由调用方预分配（execute 立即返回 id、后台运行）；
    - on_update：每个节点完成（含重试轮）后回调一次**投影后的**累计状态，
      供 workflow_runner 组装 11.2 实时快照推 WS / 轮询。

    Returns:
        ``{workflowId, finalOutput, trace}``；trace 含 11.2 的 agents/messages/stats
        + 节点日志 nodes + ragContextUsed，B7 的 ``/workflow/{id}`` 直接消费。
    """
    workflow_id = workflow_id or f"wf_{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc)
    kp = db.get(KnowledgePoint, kp_id)
    kp_name = kp.name if kp is not None else kp_id
    kp_description = (kp.description or "") if kp is not None else ""

    # checkpointer 默认 None → compile(checkpointer=None) 与迁移前 compile() 等价，
    # config 也只带 recursion_limit；启用时才额外带 thread_id（LangGraph 要求）。
    checkpointer = checkpoint.get_checkpointer()
    config = checkpoint.graph_config(workflow_id, checkpointer)
    app_graph = _build_graph(
        db,
        retriever or _mock_retriever,
        disable_critic=disable_critic,
        checkpointer=checkpointer,
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

    # stream(values)：每个超步（顺序图 = 每个节点）后产出累积 State，
    # 终态与 invoke() 等价；首个产出为输入态（execution.currentNode 为空），
    # 与迁移前 `if state.get("current_node")` 同样跳过不回调。
    final_state: MultiAgentState | None = None
    for state in app_graph.stream(initial_state, config=config, stream_mode="values"):
        final_state = state
        if on_update is None:
            continue
        if not (state.get("execution") or {}).get("currentNode"):
            continue
        on_update(project_legacy_state(state))
    if final_state is None or not (final_state.get("result") or {}):
        raise RuntimeError("工作流未产出 final_output")

    final_output = dict(final_state.get("result") or {})
    degraded = bool(final_output.get("degraded"))
    node_log = list((final_state.get("execution") or {}).get("nodeLog") or [])
    rag_context_used = list((final_state.get("evidence") or {}).get("ragContext") or [])

    trace = build_trace(
        workflow_id=workflow_id,
        messages=final_state.get("messages") or [],
        node_log=node_log,
        rag_context=rag_context_used,
        degraded=degraded,
    )
    status = "degraded" if degraded else "success"

    # 持久化（接口文档 11.2 渲染结构 + rag_context_used，B7 直接消费）
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

    return {"workflowId": workflow_id, "finalOutput": final_output, "trace": trace}
