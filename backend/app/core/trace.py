"""执行轨迹与 11.2 投影层（R2）。

## 为什么需要投影

R2 把状态里的消息从「手工 `_msg()` 拼的展示字符串」换成 **`AgentMessage` 执行流水**
（真实 task_id / agent_name / 输入输出 / traceId）。但 11.2 与前端要的是
`{id, from, to, message, type, timestamp}` —— 两者的**语义不同**：

| | `AgentMessage`（执行协议） | 11.2 `messages[]`（展示协议） |
|---|---|---|
| 关注 | 谁执行、输入什么、输出什么、耗时/置信 | 用户看到「谁对谁说了什么」 |
| 消费方 | trace 审计、R3 Supervisor、重放 | 前端对话流渲染 |

本模块是**两者的唯一换算点**。节点创建 `AgentMessage` 时把展示所需的
`from/to/message/type` 放进 `metadata["display"]`（与迁移前 `_msg()` 的入参逐字一致），
投影时原样取出 —— 于是：

- **State 里是真实执行流水**（R2 目标达成）；
- **输出的 11.2 结构与文案逐字不变**（前端完全无感）。

`project_legacy_state` 同理：把嵌套的 `MultiAgentState` 投影回扁平
`LegacyWorkflowState`，供 `workflow_runner._snapshot_from_state` 消费，
后者因此**一行未改**。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.agents.protocol import AgentMessage
from app.workflows.state import MultiAgentState

# 11.2 agents[].id 固定顺序（展示契约，非调度顺序）
AGENT_ORDER: tuple[str, ...] = ("diagnosis", "generation", "critic")
# 节点日志中渲染后 prompt 的截取长度（热更新验证 / B7 展示用）
PROMPT_EXCERPT_LEN = 400

# display 元数据键（AgentMessage.metadata 内）
_DISPLAY_KEY = "display"


# ── AgentMessage 构造 ─────────────────────────────────────────────────────────


def make_message(
    *,
    task_id: str,
    agent_name: str,
    display_from: str,
    display_to: str,
    display_message: str,
    display_type: str = "response",
    inputs: Mapping[str, Any] | None = None,
    outputs: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
    metadata: Mapping[str, Any] | None = None,
) -> AgentMessage:
    """构造带展示信息的 AgentMessage。

    Args:
        display_from / display_to / display_message / display_type：
            **与迁移前 ``_msg()`` 的入参逐字一致**，保证 11.2 文案不变。
        inputs / outputs：真实执行的结构化输入输出（进 AgentMessage，不进 11.2）。

    Returns:
        AgentMessage；展示信息存于 ``metadata["display"]``。
    """
    merged: dict[str, Any] = dict(metadata or {})
    merged[_DISPLAY_KEY] = {
        "from": display_from,
        "to": display_to,
        "message": display_message,
        "type": display_type,
    }
    return AgentMessage(
        task_id=task_id,
        agent_name=agent_name,
        input=dict(inputs or {}),
        output=dict(outputs or {}),
        confidence=confidence,
        metadata=merged,
    )


def display_of(message: AgentMessage) -> dict[str, str]:
    """取出展示字段；缺 display 时按执行者兜底（保证投影永不抛错）。"""
    display = (message.metadata or {}).get(_DISPLAY_KEY) or {}
    return {
        "from": str(display.get("from") or message.agent_name),
        "to": str(display.get("to") or "用户"),
        "message": str(display.get("message") or ""),
        "type": str(display.get("type") or "response"),
    }


# ── 11.2 投影 ─────────────────────────────────────────────────────────────────


def project_messages(messages: Sequence[AgentMessage] | Iterable[AgentMessage]) -> list[dict[str, Any]]:
    """AgentMessage 流水 → 11.2 messages[]（**不含 id**）。

    不含 ``id`` 是刻意的：迁移前 state 里的 messages 就没有 id，
    ``workflow_runner._snapshot_from_state`` 与 ``run_learning_workflow`` 两处
    各自 `{"id": i + 1, **m}` 编号。保持这一点，两个调用方零改动。
    """
    projected: list[dict[str, Any]] = []
    for message in messages:
        display = display_of(message)
        projected.append(
            {
                "from": display["from"],
                "to": display["to"],
                "message": display["message"],
                "type": display["type"],
                "timestamp": message.timestamp.isoformat(),
            }
        )
    return projected


def project_agents(
    node_log: Sequence[Mapping[str, Any]], *, degraded: bool
) -> list[dict[str, Any]]:
    """11.2 agents[]：固定 3 项；降级时 critic 标 error（红灯）。"""
    agents: list[dict[str, Any]] = []
    for agent_id in AGENT_ORDER:
        entries = [n for n in node_log if n["agent"] == agent_id]
        name = entries[-1]["name"] if entries else agent_id
        status = "error" if (degraded and agent_id == "critic") else "success"
        last_action = entries[-1]["outputSummary"] if entries else ""
        agents.append(
            {"id": agent_id, "name": name, "status": status, "lastAction": last_action}
        )
    return agents


def project_legacy_state(state: MultiAgentState | Mapping[str, Any]) -> dict[str, Any]:
    """MultiAgentState → 迁移前的扁平 WorkflowState（on_update / 11.2 快照消费）。

    这是 R2「前端完全无感」的保证：``workflow_runner._snapshot_from_state`` 读的
    5 个键（``current_node`` / ``messages`` / ``node_execution_log`` /
    ``workflow_status`` / ``validation_result``）在此逐一对上。
    """
    identity = state.get("identity") or {}
    learning = state.get("learning_context") or {}
    plan = state.get("plan") or {}
    execution = state.get("execution") or {}
    evidence = state.get("evidence") or {}
    artifacts = state.get("artifacts") or {}
    quality = state.get("quality") or {}
    result = state.get("result") or {}

    return {
        # 输入数据
        "user_id": identity.get("userId", ""),
        "kp_id": learning.get("kpId", ""),
        "kp_name": learning.get("kpName", ""),
        "kp_description": learning.get("kpDescription", ""),
        "difficulty": learning.get("difficulty", ""),
        "target_job": learning.get("targetJob", ""),
        # 消息历史（11.2 messages，不含 id）
        "messages": project_messages(state.get("messages") or []),
        # 学情诊断
        "profile": learning.get("profile"),
        # RAG 检索
        "reranked_context": evidence.get("ragContext"),
        # 内容生成
        "generated_content": artifacts.get("markdown"),
        "generation_feedback": artifacts.get("feedback"),
        # 内容校验
        "validation_result": quality.get("result"),
        "hallucination_rate": quality.get("hallucinationRate"),
        "error_details": list(state.get("errors") or []),
        # 流程控制
        "retry_count": int(execution.get("retryCount", 0)),
        "max_retries": int(plan.get("maxRetries", 0)),
        "current_node": str(execution.get("currentNode", "")),
        "workflow_status": str(execution.get("status", "")),
        # 输出结果
        "final_output": result or None,
        # 元数据
        "node_execution_log": list(execution.get("nodeLog") or []),
    }


def build_trace(
    *,
    workflow_id: str,
    messages: Sequence[AgentMessage],
    node_log: Sequence[Mapping[str, Any]],
    rag_context: Sequence[Mapping[str, Any]],
    degraded: bool,
) -> dict[str, Any]:
    """组装 11.2 完成态 trace（结构与迁移前逐字一致）。"""
    projected = project_messages(messages)
    numbered = [{"id": index + 1, **item} for index, item in enumerate(projected)]
    agents = project_agents(node_log, degraded=degraded)
    return {
        "workflowId": workflow_id,
        "phase": "complete",  # 11.2：idle|diagnosis|generation|validation|complete
        "step": 4,
        "agents": agents,
        "messages": numbered,
        "nodes": list(node_log),
        "ragContextUsed": list(rag_context),
        "stats": {
            "completedAgents": sum(1 for a in agents if a["status"] == "success"),
            "messageCount": len(numbered),
            "progress": 100,
        },
    }
