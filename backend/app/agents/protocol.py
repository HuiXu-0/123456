"""Agent间通信协议（TASK-002-B1）。

执行消息与前端展示消息分离；本模型只描述Agent基础执行结果，不改变现有11.2接口。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentMessage(BaseModel):
    """一次Agent执行的版本化基础消息。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    agent_name: str
    timestamp: datetime = Field(default_factory=utc_now)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id", "agent_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

