"""Agent抽象基类（TASK-002-B1；R1 扩展能力声明）。

历史：TASK-002-B1 建立本基类时，新框架与现有函数式Agent并行存在、不迁移业务逻辑。
R1（统一 Agent Framework）之后：本基类是 **7 个业务 Agent 的唯一执行抽象**
（经 ``app.agents.adapters`` 收编），并新增 ``capabilities`` / ``tools`` / ``prompt_id``
三个声明字段，作为 ``app.agents.registry`` 目录与 R3 Supervisor 分发的依据。
业务逻辑本身仍留在各 Agent 模块，本层只做 Schema 校验、日志、trace 与消息产出。
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.agents.protocol import AgentMessage
from app.agents.state import AgentState
from app.core.envelope import current_trace_id

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class BaseAgent(ABC, Generic[InputT, OutputT]):
    """带Schema校验、统一日志和traceId的Agent执行模板。

    R1 增补（只增不改）：``capabilities`` / ``tools`` / ``prompt_id`` 三个声明字段，
    让每个 Agent **自己声明"我会什么、能调什么工具、用哪份 Prompt"**——这正是
    ``app.agents.registry`` 与 R3 Supervisor 的分发依据。三者都带默认值，既有
    Adapter 子类无需改动即可通过 ``_validate_definition``。
    """

    agent_name: str
    description: str
    input_schema: type[InputT]
    output_schema: type[OutputT]
    # 能力声明（非空才有分发价值；由注册层强制校验）
    capabilities: tuple[str, ...] = ()
    # 可调工具名，对应 app.agents.tools.ToolRegistry；R1 为空，R4 接入
    tools: tuple[str, ...] = ()
    # Prompt 归属 agentId；None 表示该 Agent 的 prompt 在 LLMClient 侧（不走模板渲染）
    prompt_id: str | None = None

    def __init__(
        self,
        *,
        trace_id: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.trace_id = trace_id or current_trace_id() or f"agt_{uuid.uuid4().hex[:12]}"
        self.logger = logger or logging.getLogger(
            f"app.agents.{getattr(self, 'agent_name', type(self).__name__)}"
        )
        self._validate_definition()

    def _validate_definition(self) -> None:
        for name in ("agent_name", "description", "input_schema", "output_schema"):
            if not hasattr(self, name):
                raise TypeError(f"{type(self).__name__} must define {name}")
        if not self.agent_name.strip() or not self.description.strip():
            raise TypeError("agent_name and description must not be blank")
        if not issubclass(self.input_schema, BaseModel):
            raise TypeError("input_schema must be a Pydantic BaseModel")
        if not issubclass(self.output_schema, BaseModel):
            raise TypeError("output_schema must be a Pydantic BaseModel")
        # R1：能力/工具/Prompt 声明的类型校验（声明本身必须可信，否则分发层会被污染）
        for name in ("capabilities", "tools"):
            value = getattr(self, name, ())
            if not isinstance(value, tuple) or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise TypeError(f"{name} must be a tuple of non-blank str")
        if self.prompt_id is not None and not str(self.prompt_id).strip():
            raise TypeError("prompt_id must be None or a non-blank str")

    def manifest(self) -> dict[str, Any]:
        """能力清单（registry / trace / Supervisor 内省用）。"""
        return {
            "agentId": self.agent_name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "promptId": self.prompt_id,
            "inputSchema": self.input_schema.__name__,
            "outputSchema": self.output_schema.__name__,
            "traceId": self.trace_id,
        }

    def log_event(self, event: str, **details: Any) -> None:
        """统一Agent日志接口；不记录完整输入输出，避免隐私和大文本泄漏。"""
        suffix = " ".join(f"{key}={value}" for key, value in sorted(details.items()))
        self.logger.info(
            "agent_event event=%s agent=%s traceId=%s%s",
            event,
            self.agent_name,
            self.trace_id,
            f" {suffix}" if suffix else "",
        )

    @abstractmethod
    def execute(self, agent_input: InputT, state: AgentState) -> OutputT:
        """实现单次Agent能力；不得绕过声明的输入输出Schema。"""
        raise NotImplementedError

    def run(
        self,
        *,
        task_id: str,
        agent_input: InputT | dict[str, Any],
        state: AgentState | dict[str, Any] | None = None,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """校验输入和状态，执行Agent并生成统一消息。"""
        validated_input = self.input_schema.model_validate(agent_input)
        validated_state = AgentState.model_validate(state or {})
        self.log_event("started", task_id=task_id)
        try:
            raw_output = self.execute(validated_input, validated_state)
            validated_output = self.output_schema.model_validate(raw_output)
        except Exception:
            self.logger.exception(
                "agent_event event=failed agent=%s traceId=%s task_id=%s",
                self.agent_name,
                self.trace_id,
                task_id,
            )
            raise
        self.log_event("completed", task_id=task_id)
        message_metadata = dict(metadata or {})
        message_metadata.setdefault("traceId", self.trace_id)
        message_metadata.setdefault("description", self.description)
        return AgentMessage(
            task_id=task_id,
            agent_name=self.agent_name,
            input=validated_input.model_dump(mode="json"),
            output=validated_output.model_dump(mode="json"),
            confidence=confidence,
            metadata=message_metadata,
        )

