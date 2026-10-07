"""并行学习任务Agent子图（TASK-002-B3）。

本图只消费新BaseAgent/Adapter体系，不替换也不导入旧learning_workflow。

> **R1 归属说明（不改逻辑）**：本图自 TASK-002-B3 起**未接入任何 API 路由**，
> 全仓引用方仅 `app/workflows/test_learning_graph.py`。它是与生产
> `learning_workflow` **平行的第二套拓扑**（critic 决策枚举为 PASS/REVISE/FALLBACK，
> 且多 evaluation 节点），二者会各自漂移。R1 已把两者共用的 BaseAgent/Adapter
> 收敛到 `app.agents.registry` 单一目录；本图的去留属于 **R2（工作流迁移）**
> 决策范围，在 R1 阶段**保留不删**。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents.adapters import (
    CriticAgentAdapter,
    EvaluationAgentAdapter,
    LearningDiagnosisAgentAdapter,
    QualityDecision,
    ResourceGenerationAgentAdapter,
)
from app.agents.protocol import AgentMessage
from app.agents.state import AgentState
from app.core.envelope import current_trace_id
from app.workflows.base import BaseWorkflow


class LearningGraphState(AgentState):
    """第一个学习任务子图状态；扩展B1基础状态而不改变其公共契约。"""

    model_config = ConfigDict(extra="forbid")

    resources: list[dict[str, Any]] = Field(default_factory=list)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    quality_decision: QualityDecision | None = None
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    fallback: dict[str, Any] = Field(default_factory=dict)


class LearningTaskWorkflow(BaseWorkflow[LearningGraphState]):
    """带质量条件路由的并行学习任务子图。"""

    workflow_name = "learning_task_agent_graph"
    state_schema = LearningGraphState

    def __init__(self, db: Session, *, trace_id: str | None = None) -> None:
        self.db = db
        self.trace_id = (
            trace_id or current_trace_id() or f"lwf_{uuid.uuid4().hex[:12]}"
        )
        self._graph = None
        super().__init__()

    @staticmethod
    def _required(mapping: dict[str, Any], key: str) -> Any:
        value = mapping.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"学习子图缺少必要上下文：{key}")
        return value

    @staticmethod
    def _history(
        state: LearningGraphState, message: AgentMessage
    ) -> list[AgentMessage]:
        return [*state.execution_history, message]

    def _task_id(self, state: LearningGraphState, stage: str) -> str:
        root = str(state.task_context.get("task_id") or self.trace_id)
        return f"{root}:{stage}"

    def diagnosis_node(self, state: LearningGraphState) -> dict[str, Any]:
        task = state.task_context
        user = state.user_context
        mastery = task.get("mastery_status", state.knowledge_state)
        if not isinstance(mastery, str):
            mastery = json.dumps(mastery, ensure_ascii=False, default=str)
        message = LearningDiagnosisAgentAdapter(
            self.db, trace_id=self.trace_id
        ).run(
            task_id=self._task_id(state, "diagnosis"),
            agent_input={
                "kp_id": self._required(task, "kp_id"),
                "kp_name": task.get("kp_name", ""),
                "profile_summary": user.get(
                    "profile_summary", "画像尚未采集（按通用基线诊断）"
                ),
                "mastery_status": mastery,
                "target_job": task.get("target_job", "大模型应用工程师"),
            },
            state=state,
            metadata={"workflow": self.workflow_name, "node": "diagnosis"},
        )
        knowledge_state = dict(state.knowledge_state)
        knowledge_state["diagnosis"] = message.output
        knowledge_state["rag_context"] = task.get("rag_context", [])
        return {
            "knowledge_state": knowledge_state,
            "execution_history": self._history(state, message),
        }

    def resource_node(self, state: LearningGraphState) -> dict[str, Any]:
        task = state.task_context
        user = state.user_context
        feedback = task.get("feedback")
        if state.quality_decision == QualityDecision.REVISE:
            issues = state.quality.get("issues") or []
            if issues:
                feedback = "\n".join(str(item) for item in issues)
        message = ResourceGenerationAgentAdapter(
            self.db, trace_id=self.trace_id
        ).run(
            task_id=self._task_id(state, "resource"),
            agent_input={
                "kp_name": self._required(task, "kp_name"),
                "difficulty": self._required(task, "difficulty"),
                "rag_context": task.get("rag_context", []),
                "feedback": feedback,
                "description": task.get("description", ""),
                "learner_profile": user.get("profile_summary", ""),
                "depth_tier": task.get("depth_tier"),
            },
            state=state,
            metadata={"workflow": self.workflow_name, "node": "resource"},
        )
        return {
            "resources": [*state.resources, message.output],
            "execution_history": self._history(state, message),
        }

    def critic_node(self, state: LearningGraphState) -> dict[str, Any]:
        message = CriticAgentAdapter(self.db, trace_id=self.trace_id).run(
            task_id=self._task_id(state, "critic"),
            agent_input={
                "learning_goal": state.learning_goal,
                "knowledge_state": state.knowledge_state,
                "resources": state.resources,
            },
            state=state,
            metadata={"workflow": self.workflow_name, "node": "critic"},
        )
        decision = QualityDecision(message.output["decision"])
        retry_count = state.retry_count
        if decision == QualityDecision.REVISE:
            if retry_count < state.max_retries:
                retry_count += 1
            else:
                decision = QualityDecision.FALLBACK
                final_output = {
                    **message.output,
                    "decision": decision.value,
                    "reason": (
                        f"质量校验未通过且已达到最大重试次数{state.max_retries}"
                    ),
                }
                message = message.model_copy(update={"output": final_output})
        return {
            "quality": message.output,
            "quality_decision": decision,
            "retry_count": retry_count,
            "execution_history": self._history(state, message),
        }

    @staticmethod
    def route_after_critic(state: LearningGraphState) -> str:
        decision = state.quality_decision or QualityDecision.FALLBACK
        return decision.value

    def fallback_node(self, state: LearningGraphState) -> dict[str, Any]:
        latest_resource = state.resources[-1] if state.resources else {}
        fallback = {
            "degraded": True,
            "requiresReview": True,
            "reason": state.quality.get("reason", "质量校验未通过"),
            "resource": latest_resource,
            "retryCount": state.retry_count,
        }
        message = AgentMessage(
            task_id=self._task_id(state, "fallback"),
            agent_name="fallback_handler",
            input={
                "quality": state.quality,
                "retryCount": state.retry_count,
            },
            output=fallback,
            confidence=0.0,
            metadata={
                "workflow": self.workflow_name,
                "node": "fallback",
                "traceId": self.trace_id,
            },
        )
        return {
            "fallback": fallback,
            "execution_history": self._history(state, message),
        }

    def evaluation_node(self, state: LearningGraphState) -> dict[str, Any]:
        user_id = self._required(state.user_context, "user_id")
        message = EvaluationAgentAdapter(
            self.db, trace_id=self.trace_id
        ).run(
            task_id=self._task_id(state, "evaluation"),
            agent_input={"user_id": user_id},
            state=state,
            metadata={"workflow": self.workflow_name, "node": "evaluation"},
        )
        return {
            "evaluation": message.output,
            "execution_history": self._history(state, message),
        }

    def build(self):
        if self._graph is None:
            builder = StateGraph(LearningGraphState)
            builder.add_node("diagnosis", self.diagnosis_node)
            builder.add_node("resource", self.resource_node)
            builder.add_node("critic", self.critic_node)
            builder.add_node("evaluation", self.evaluation_node)
            builder.add_node("fallback", self.fallback_node)
            builder.add_edge(START, "diagnosis")
            builder.add_edge("diagnosis", "resource")
            builder.add_edge("resource", "critic")
            builder.add_conditional_edges(
                "critic",
                self.route_after_critic,
                {
                    QualityDecision.PASS.value: "evaluation",
                    QualityDecision.REVISE.value: "resource",
                    QualityDecision.FALLBACK.value: "fallback",
                },
            )
            builder.add_edge("evaluation", END)
            builder.add_edge("fallback", END)
            self._graph = builder.compile()
        return self._graph

    def execute(
        self, state: LearningGraphState | dict[str, Any]
    ) -> LearningGraphState:
        initial = self.validate_state(state)
        result = self.build().invoke(
            initial,
            config={"recursion_limit": max(10, initial.max_retries * 3 + 10)},
        )
        return self.state_schema.model_validate(result)
