"""Workflow基础接口（TASK-002-B1）。

仅定义新工作流的公共边界；现有learning_workflow不在本阶段迁移。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from app.agents.state import AgentState

StateT = TypeVar("StateT", bound=AgentState)


class BaseWorkflow(ABC, Generic[StateT]):
    """新Agent工作流必须实现的最小接口。"""

    workflow_name: str
    state_schema: type[StateT]

    def __init__(self) -> None:
        if not getattr(self, "workflow_name", "").strip():
            raise TypeError(f"{type(self).__name__} must define workflow_name")
        schema = getattr(self, "state_schema", None)
        if not isinstance(schema, type) or not issubclass(schema, AgentState):
            raise TypeError("state_schema must inherit AgentState")

    def validate_state(self, state: StateT | dict[str, Any]) -> StateT:
        return self.state_schema.model_validate(state)

    @abstractmethod
    def build(self) -> Any:
        """构建并返回具体工作流图。"""
        raise NotImplementedError

    @abstractmethod
    def execute(self, state: StateT | dict[str, Any]) -> StateT:
        """校验初始状态并执行工作流。"""
        raise NotImplementedError

