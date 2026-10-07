"""Agent共享状态结构（TASK-002-B1）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.protocol import AgentMessage


class AgentState(BaseModel):
    """与具体业务解耦的最小Agent状态。

    五个字段对应TASK-002-B1约定。状态更新采用返回副本的方式，避免节点间原地
    修改执行历史；具体LangGraph State迁移留给后续阶段。
    """

    model_config = ConfigDict(extra="forbid")

    user_context: dict[str, Any] = Field(default_factory=dict)
    learning_goal: str = ""
    knowledge_state: dict[str, Any] = Field(default_factory=dict)
    task_context: dict[str, Any] = Field(default_factory=dict)
    execution_history: list[AgentMessage] = Field(default_factory=list)

    def record(self, message: AgentMessage) -> "AgentState":
        """返回追加一条不可变执行事件后的新状态。"""
        data = self.model_dump()
        data["execution_history"] = [*self.execution_history, message]
        return type(self).model_validate(data)

