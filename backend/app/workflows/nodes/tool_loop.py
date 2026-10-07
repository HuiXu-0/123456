"""Tool Loop 的 LangGraph 接入（R4.3）——**跑在既有 Runtime 内，不另起一套**。

## 图结构（与规格 §9 一致）

```
START ─▶ agent_decision ──┬─ kind=tool_call ──────▶ tool_execute ─┐
                          │                                        │
                          ├─ kind=invalid/error ──▶ agent_decision ◀┘（回喂，消耗一步）
                          │
                          └─ kind=final_answer /
                               budget_exhausted ───▶ final ──▶ END
```

- **决策与执行是两个节点**，由条件边连接 —— 「没有 Tool Call 就不进
  ``tool_execute``」这条由 ``route()`` 结构性保证（规格 §12-14）；
- **``tool_step`` 在决策节点自增**，``agent_decision`` 先判预算：
  ``tool_step >= MAX_TOOL_STEPS`` → ``budget_exhausted`` → 直接 ``final``，
  由 ``for/else`` 语义的等价判定实现「达到 3 次必须停止」；
- 复用 **``ToolLoopState``（= ``MultiAgentState`` + 4 个 Tool 字段）**，
  不另起 State，也不改 ``MultiAgentState``（被 R2 十字段契约锁死）。

## 安全边界不变

执行仍只经 ``ToolLoop.execute`` → ``ToolRegistry.execute()``；
权限仍取 ``BaseAgent.tools ∩ 已注册 Spec``。图只是**编排**，不接管安全判定。

## 未接入生产端点

本图是**独立可调用的子图**，R4.3 **没有**把它接进 ``/workflow/execute``
——— 那属于改变既有工作流行为，需单独批准（同 R3.5 的灰度姿势）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from app.agents.tool_loop import (
    MAX_TOOL_STEPS,
    FinalAnswer,
    Observation,
    StepRecord,
    ToolCall,
    ToolLoop,
    ToolLoopResult,
)
from app.workflows.state import ToolLoopState

logger = logging.getLogger("app.workflows.nodes.tool_loop")

__all__ = [
    "MAX_TOOL_STEPS",
    "ToolLoopResult",
    "build_tool_loop_graph",
    "initial_tool_loop_state",
    "run_tool_loop",
]


def initial_tool_loop_state(
    *, actor_id: str, task: str, workflow_id: str = "wfl_local"
) -> ToolLoopState:
    """Tool Loop 的初始状态（**所有带 reducer 的字段显式给初值**）。"""
    return {
        "identity": {"userId": actor_id, "workflowId": workflow_id, "sessionId": None},
        "learning_context": {"objective": task},
        "plan": {"maxRetries": 0},
        "execution": {"currentNode": "", "status": "pending", "retryCount": 0, "nodeLog": []},
        "evidence": {"ragContext": [], "references": []},
        "messages": [],
        "artifacts": {"markdown": "", "feedback": None},
        "quality": {"result": {}, "hallucinationRate": None},
        "errors": [],
        "result": {},
        "tool_calls": [],
        "tool_results": [],
        "tool_step": 0,
        "tool_decision": {},
    }


def _history_of(state: ToolLoopState) -> list[dict[str, Any]]:
    """从 State 重建决策源可见的历史（tool_call / observation 配对）。"""
    history: list[dict[str, Any]] = [dict(item) for item in (state.get("tool_calls") or [])]
    history.extend(dict(item) for item in (state.get("tool_results") or []))
    # 上一轮的非法决策错误 → 回喂给模型（规格 §6：拒绝后反馈错误）
    previous = state.get("tool_decision") or {}
    feedback = previous.get("feedback")
    if feedback:
        history.append({"kind": "observation", "error": feedback, "step": previous.get("step")})
    return history


def build_tool_loop_graph(loop: ToolLoop):
    """装配并编译 Tool Loop 图。

    Args:
        loop: 已绑定 ``agent_id`` / ``actor_id`` 的 :class:`ToolLoop`（闭包捕获，
            与既有 ``build_graph(db, retriever)`` 同款写法）。

    Returns:
        编译后的 LangGraph 图（schema = ``ToolLoopState``）。
    """

    def agent_decision(state: ToolLoopState) -> dict[str, Any]:
        """LLM 决策节点：写 ``tool_decision``，**先判预算再决策**。"""
        current_step = int(state.get("tool_step") or 0)
        task = str((state.get("learning_context") or {}).get("objective") or "")

        # 预算耗尽（等价于 for/else：上一轮已用满 3 步）→ 直接收敛，不再问模型
        if current_step >= loop.max_steps:
            logger.info("tool_loop_budget_exhausted agent=%s step=%s", loop.agent_id, current_step)
            return {
                "tool_decision": {
                    "kind": "budget_exhausted",
                    "step": current_step,
                    "reason": f"已达 MAX_TOOL_STEPS={loop.max_steps}",
                },
                "execution": {"currentNode": "tool_loop:decision", "status": "running"},
            }

        started = time.perf_counter()
        try:
            decision = loop.decide(task, _history_of(state))
        except Exception as exc:  # noqa: BLE001 — 决策源故障：回喂，不崩图
            error = f"决策失败：{type(exc).__name__}: {exc}"
            logger.warning("tool_loop_decision_error agent=%s error=%s", loop.agent_id, error)
            return {
                "tool_step": current_step + 1,
                "tool_decision": {"kind": "decision_error", "step": current_step + 1, "feedback": error},
                "errors": [{"node": "agent_decision", "error": error, "step": current_step + 1}],
                "execution": {"currentNode": "tool_loop:decision", "status": "running"},
            }

        if decision is None:
            error = "模型输出不符合结构化决策契约（需 kind=tool_call 或 final_answer）"
            logger.warning("tool_loop_invalid_decision agent=%s step=%s", loop.agent_id, current_step + 1)
            return {
                "tool_step": current_step + 1,
                "tool_decision": {"kind": "invalid", "step": current_step + 1, "feedback": error},
                "errors": [{"node": "agent_decision", "error": error, "step": current_step + 1}],
                "execution": {"currentNode": "tool_loop:decision", "status": "running"},
            }

        if isinstance(decision, FinalAnswer):
            return {
                "tool_step": current_step + 1,
                "tool_decision": {
                    "kind": "final_answer",
                    "step": current_step + 1,
                    "answer": decision.answer,
                },
                "execution": {"currentNode": "tool_loop:decision", "status": "running"},
            }

        assert isinstance(decision, ToolCall)
        return {
            "tool_step": current_step + 1,
            "tool_decision": {
                "kind": "tool_call",
                "step": current_step + 1,
                "tool": decision.tool,
                "arguments": dict(decision.arguments or {}),
                "durationMs": round((time.perf_counter() - started) * 1000, 2),
            },
            "execution": {"currentNode": "tool_loop:decision", "status": "running"},
        }

    def tool_execute(state: ToolLoopState) -> dict[str, Any]:
        """工具执行节点：**只经 ``ToolLoop.execute`` → ``ToolRegistry.execute``**。"""
        payload = dict(state.get("tool_decision") or {})
        step = int(payload.get("step") or (state.get("tool_step") or 1))
        try:
            call = ToolCall.model_validate({
                "kind": "tool_call",
                "tool": str(payload.get("tool") or ""),
                "arguments": dict(payload.get("arguments") or {}),
            })
        except Exception as exc:  # noqa: BLE001 — 结构非法：转为 Observation，不抛
            observation = Observation(
                success=False,
                tool=str(payload.get("tool") or ""),
                arguments=dict(payload.get("arguments") or {}),
                error=str(exc) or type(exc).__name__,
                error_type=type(exc).__name__,
                step=step,
            )
        else:
            observation = loop.execute(call, step=step)

        call_id = f"call_{step}_{observation.tool}"
        return {
            "tool_calls": [{
                "kind": "tool_call",
                "id": call_id,
                "tool": observation.tool,
                "arguments": observation.arguments,
                "step": step,
            }],
            "tool_results": [{
                "kind": "observation",
                "id": call_id,
                "tool": observation.tool,
                "content": json.dumps(
                    observation.data if observation.success else {"error": observation.error},
                    ensure_ascii=False,
                    default=str,
                ),
                "degraded": observation.degraded,
                "error": observation.error,
                "step": step,
            }],
            "tool_decision": {"kind": "executed", "step": step},
            "execution": {"currentNode": "tool_loop:execute", "status": "running"},
        }

    def final_node(state: ToolLoopState) -> dict[str, Any]:
        """收敛节点：产出 ``ToolLoopResult`` 形状的 ``result``（**不抛异常**）。"""
        payload = dict(state.get("tool_decision") or {})
        kind = str(payload.get("kind") or "")
        tool_results = list(state.get("tool_results") or [])
        tool_calls = list(state.get("tool_calls") or [])
        step = int(state.get("tool_step") or 0)

        answer = ""
        deliverable = False
        truncated = False
        fallback_reason: str | None = None

        if kind == "final_answer":
            answer = str(payload.get("answer") or "")
            deliverable = bool(answer.strip())
        else:
            # 预算耗尽 / 非法决策 / 决策错误 → 按规格交付**最后一次 Observation**
            truncated = True
            fallback_reason = {
                "budget_exhausted": "max_tool_steps_exceeded",
                "invalid": "invalid_decision",
                "decision_error": "decision_error",
            }.get(kind, "no_result")
            if tool_results:
                last = tool_results[-1]
                answer = str(last.get("content") or "")
                deliverable = bool(answer.strip())
                fallback_reason = fallback_reason or "truncated_last_observation"
            if not deliverable:
                fallback_reason = fallback_reason or "no_result"

        last_tool = tool_calls[-1].get("tool") if tool_calls else None
        final_observation = tool_results[-1] if tool_results else None
        status = "degraded" if (truncated or not deliverable) else "success"

        return {
            "result": {
                "answer": answer,
                "deliverable": deliverable,
                "truncated": truncated,
                "toolSteps": step,
                "toolCalls": sum(1 for c in tool_calls if c.get("tool")),
                "lastTool": last_tool,
                "fallbackReason": fallback_reason,
                "finalObservation": final_observation,
                "degraded": truncated or not deliverable,
            },
            "execution": {"currentNode": "tool_loop:final", "status": status},
        }

    def route(state: ToolLoopState) -> str:
        """条件边：**没有 tool_call 就不进执行节点**（规格 §12-14）。"""
        kind = str(((state.get("tool_decision") or {}).get("kind")) or "")
        if kind == "tool_call":
            return "tool_execute"
        if kind in ("invalid", "decision_error"):
            return "retry"  # 回喂后重问，消耗的那一步已计数 → 有界
        return "final"  # final_answer / budget_exhausted / executed

    graph = StateGraph(ToolLoopState)
    graph.add_node("agent_decision", agent_decision)
    graph.add_node("tool_execute", tool_execute)
    graph.add_node("final", final_node)

    graph.add_edge(START, "agent_decision")
    graph.add_conditional_edges(
        "agent_decision",
        route,
        {
            "tool_execute": "tool_execute",
            "retry": "agent_decision",
            "final": "final",
        },
    )
    graph.add_edge("tool_execute", "agent_decision")
    graph.add_edge("final", END)
    return graph.compile()


def run_tool_loop(
    *,
    agent_id: str,
    actor_id: str,
    task: str,
    registry: Any | None = None,
    decision_provider: Callable[..., dict[str, Any]] | None = None,
    max_steps: int = MAX_TOOL_STEPS,
) -> ToolLoopResult:
    """**图驱动**地跑一次 Tool Loop，返回与自研 ``ToolLoop.run()`` 同构的结果。

    两条路径共用同一个 :class:`ToolLoop`（决策、校验、执行逻辑完全一致），
    区别只在**编排位置**：本函数经 LangGraph 图跑，``ToolLoop.run()`` 是命令式跑。
    测试对两者的等价性各有覆盖。
    """
    loop = ToolLoop(
        agent_id=agent_id,
        actor_id=actor_id,
        registry=registry,
        decision_provider=decision_provider,
        max_steps=max_steps,
    )
    graph = build_tool_loop_graph(loop)
    initial = initial_tool_loop_state(actor_id=actor_id, task=task)
    final_state = graph.invoke(initial, config={"recursion_limit": max_steps * 4 + 10})

    result = dict(final_state.get("result") or {})
    history = list(final_state.get("tool_calls") or []) + list(
        final_state.get("tool_results") or []
    )
    steps: list[StepRecord] = []
    for item in history:
        kind = str(item.get("kind") or "")
        steps.append(
            StepRecord(
                step=int(item.get("step") or 0),
                agent=loop.agent_id,
                decision="tool_call" if kind == "tool_call" else "observation",
                tool=str(item.get("tool") or "") or None,
                arguments=dict(item.get("arguments") or {}),
                success=kind == "tool_call" and not item.get("error"),
                degraded=bool(item.get("degraded")),
                error=item.get("error"),
            )
        )
    return ToolLoopResult(
        answer=str(result.get("answer") or ""),
        deliverable=bool(result.get("deliverable")),
        truncated=bool(result.get("truncated")),
        tool_steps=int(result.get("toolSteps") or 0),
        tool_calls=int(result.get("toolCalls") or 0),
        last_tool=result.get("lastTool"),
        fallback_reason=result.get("fallbackReason"),
        steps=steps,
        history=history,
        final_observation=result.get("finalObservation"),
    )
