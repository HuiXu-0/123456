"""TASK-002-B4 学习任务质量路由子图测试。"""
from __future__ import annotations

import pytest

from app.agents import critic_agent, diagnostic_agent, evaluation_agent, generator_agent
from app.agents.adapters import QualityDecision
from app.workflows.learning_graph import LearningGraphState, LearningTaskWorkflow


def _state(*, max_retries: int = 2) -> LearningGraphState:
    return LearningGraphState(
        user_context={"user_id": "u_1", "profile_summary": "基础一般"},
        learning_goal="掌握神经网络",
        knowledge_state={"nn": "weak"},
        task_context={
            "task_id": "task_1",
            "kp_id": "nn",
            "kp_name": "神经网络",
            "difficulty": "初级",
            "target_job": "算法工程师",
            "rag_context": [{"id": "c1", "content": "神经网络证据"}],
        },
        max_retries=max_retries,
    )


def _install_legacy_stubs(monkeypatch, calls, critic_passes):
    decisions = iter(critic_passes)

    def diagnosis(db, **kwargs):
        calls.append(("diagnosis", kwargs))
        return {"agentId": "diagnosis", "name": "诊断", "prompt": "p", "output": {"weakKpIds": ["nn"]}}

    def generation(db, **kwargs):
        calls.append(("resource", kwargs))
        return {"agentId": "generation", "name": "生成", "prompt": "p", "output": {"markdown": "# 神经网络"}}

    def critic(db, **kwargs):
        calls.append(("critic", kwargs))
        passed = next(decisions)
        return {
            "agentId": "critic", "name": "质量", "prompt": "p",
            "output": {
                "passed": passed,
                "validationScore": 0.9 if passed else 0.4,
                "hallucinationRate": 0.1 if passed else 0.6,
                "issues": [] if passed else ["证据不足"],
            },
        }

    def evaluation(db, user_id):
        calls.append(("evaluation", {"user_id": user_id}))
        return {"overallScore": 60, "level": "成长中", "trend": "stable", "dimensions": [], "weakPoints": [], "summary": "继续学习", "suggestions": [], "adjustment": {}, "generatedBy": "mock", "signals": {}}

    monkeypatch.setattr(diagnostic_agent, "run_diagnostic", diagnosis)
    monkeypatch.setattr(generator_agent, "run_generator", generation)
    monkeypatch.setattr(critic_agent, "run_critic", critic)
    monkeypatch.setattr(evaluation_agent, "evaluate", evaluation)


def test_workflow_builds_quality_routing_graph():
    graph = LearningTaskWorkflow(object(), trace_id="trace-graph").build().get_graph()
    assert {"diagnosis", "resource", "critic", "evaluation", "fallback"}.issubset(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert {("__start__", "diagnosis"), ("diagnosis", "resource"), ("resource", "critic"), ("critic", "evaluation"), ("critic", "resource"), ("critic", "fallback"), ("evaluation", "__end__"), ("fallback", "__end__")}.issubset(edges)


def test_pass_path(monkeypatch):
    calls = []
    _install_legacy_stubs(monkeypatch, calls, [True])
    result = LearningTaskWorkflow(object(), trace_id="trace-pass").execute(_state())

    assert [name for name, _ in calls] == ["diagnosis", "resource", "critic", "evaluation"]
    assert result.quality_decision == QualityDecision.PASS
    assert result.evaluation["overallScore"] == 60
    assert [message.agent_name for message in result.execution_history] == ["learning_diagnosis", "resource_generation", "quality_critic", "evaluation"]
    assert {message.metadata["traceId"] for message in result.execution_history} == {"trace-pass"}


def test_revise_path_regenerates_with_critic_feedback(monkeypatch):
    calls = []
    _install_legacy_stubs(monkeypatch, calls, [False, True])
    result = LearningTaskWorkflow(object(), trace_id="trace-revise").execute(_state())

    assert [name for name, _ in calls] == ["diagnosis", "resource", "critic", "resource", "critic", "evaluation"]
    assert result.retry_count == 1
    assert len(result.resources) == 2
    assert [kwargs for name, kwargs in calls if name == "resource"][1]["feedback"] == "证据不足"
    assert result.quality_decision == QualityDecision.PASS


def test_fallback_path(monkeypatch):
    calls = []
    _install_legacy_stubs(monkeypatch, calls, [False])
    result = LearningTaskWorkflow(object(), trace_id="trace-fallback").execute(_state(max_retries=0))

    assert [name for name, _ in calls] == ["diagnosis", "resource", "critic"]
    assert result.quality_decision == QualityDecision.FALLBACK
    assert result.fallback["degraded"] is True
    assert not result.evaluation
    assert result.execution_history[-1].agent_name == "fallback_handler"
    assert {message.metadata["traceId"] for message in result.execution_history} == {"trace-fallback"}


def test_max_retry_limit_prevents_infinite_loop(monkeypatch):
    calls = []
    _install_legacy_stubs(monkeypatch, calls, [False, False, False])
    result = LearningTaskWorkflow(object(), trace_id="trace-limit").execute(_state(max_retries=2))

    assert [name for name, _ in calls].count("resource") == 3
    assert [name for name, _ in calls].count("critic") == 3
    assert result.retry_count == 2
    assert result.quality_decision == QualityDecision.FALLBACK
    assert len(result.execution_history) == 8


def test_missing_required_context_fails_explicitly(monkeypatch):
    calls = []
    _install_legacy_stubs(monkeypatch, calls, [True])
    state = _state().model_copy(update={"task_context": {"kp_id": "nn", "kp_name": "神经网络"}})
    with pytest.raises(ValueError, match="difficulty"):
        LearningTaskWorkflow(object()).execute(state)
