"""TASK-002-B1 Agent基础框架专项单元测试。"""
from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.agents.base import BaseAgent
from app.agents.protocol import AgentMessage
from app.agents.state import AgentState
from app.workflows.base import BaseWorkflow


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class EchoOutput(BaseModel):
    text: str


class EchoAgent(BaseAgent[EchoInput, EchoOutput]):
    agent_name = "echo"
    description = "用于验证基础框架，不承载业务逻辑"
    input_schema = EchoInput
    output_schema = EchoOutput

    def execute(self, agent_input: EchoInput, state: AgentState) -> EchoOutput:
        return EchoOutput(text=agent_input.text)


class EmptyWorkflow(BaseWorkflow[AgentState]):
    workflow_name = "empty"
    state_schema = AgentState

    def build(self):
        return "empty-graph"

    def execute(self, state):
        return self.validate_state(state)


def test_agent_message_validates_confidence_and_required_names():
    message = AgentMessage(task_id=" t_1 ", agent_name=" echo ", confidence=0.8)
    assert message.task_id == "t_1"
    assert message.agent_name == "echo"
    assert message.timestamp.tzinfo is not None
    with pytest.raises(ValidationError):
        AgentMessage(task_id="t", agent_name="echo", confidence=1.1)
    with pytest.raises(ValidationError):
        AgentMessage(task_id=" ", agent_name="echo")


def test_agent_state_uses_isolated_defaults_and_returns_copy():
    left, right = AgentState(), AgentState()
    left.user_context["userId"] = "u_1"
    assert right.user_context == {}
    message = AgentMessage(task_id="t_1", agent_name="echo")
    updated = left.record(message)
    assert left.execution_history == []
    assert updated.execution_history == [message]


def test_base_agent_validates_schema_logs_and_propagates_trace(caplog):
    agent = EchoAgent(trace_id="trace-b1")
    with caplog.at_level(logging.INFO):
        message = agent.run(
            task_id="t_1",
            agent_input={"text": "hello"},
            state={"learning_goal": "learn"},
            confidence=0.9,
        )
    assert message.output == {"text": "hello"}
    assert message.metadata["traceId"] == "trace-b1"
    assert "event=started" in caplog.text
    assert "event=completed" in caplog.text
    with pytest.raises(ValidationError):
        agent.run(task_id="t_2", agent_input={"text": "x", "extra": True})


def test_base_agent_remains_abstract():
    with pytest.raises(TypeError):
        BaseAgent()


def test_base_workflow_contract_and_state_validation():
    workflow = EmptyWorkflow()
    assert workflow.build() == "empty-graph"
    result = workflow.execute({"learning_goal": "掌握神经网络"})
    assert isinstance(result, AgentState)
    assert result.learning_goal == "掌握神经网络"
    with pytest.raises(TypeError):
        BaseWorkflow()

