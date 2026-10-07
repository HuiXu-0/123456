"""TASK-002-B2 Adapter新旧结果一致性测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents import critic_agent, diagnostic_agent, evaluation_agent, generator_agent
from app.agents.adapters import (
    CriticAgentAdapter,
    EvaluationAgentAdapter,
    LearningDiagnosisAgentAdapter,
    ResourceGenerationAgentAdapter,
    QualityDecision,
)


def test_learning_diagnosis_adapter_matches_legacy_result(monkeypatch):
    calls = []

    def legacy(db, **kwargs):
        calls.append((db, kwargs))
        return {
            "agentId": "diagnosis",
            "name": "学情诊断Agent",
            "prompt": "diagnostic prompt",
            "output": {"weakKpIds": ["nn"], "summary": "需要补强"},
        }

    monkeypatch.setattr(diagnostic_agent, "run_diagnostic", legacy)
    db = object()
    kwargs = {
        "kp_id": "nn",
        "kp_name": "神经网络",
        "profile_summary": "基础一般",
        "mastery_status": '{"nn":"weak"}',
        "target_job": "算法工程师",
    }
    old_result = legacy(db, **kwargs)
    message = LearningDiagnosisAgentAdapter(db, trace_id="trace-diag").run(
        task_id="task-diag", agent_input=kwargs
    )
    assert message.output == old_result
    assert calls[0] == calls[1]
    assert message.metadata["traceId"] == "trace-diag"


def test_resource_generation_adapter_matches_legacy_result(monkeypatch):
    calls = []

    def legacy(db, **kwargs):
        calls.append((db, kwargs))
        return {
            "agentId": "generation",
            "name": "领域知识生成Agent",
            "prompt": "generation prompt",
            "output": {"markdown": "# 神经网络"},
        }

    monkeypatch.setattr(generator_agent, "run_generator", legacy)
    db = object()
    kwargs = {
        "kp_name": "神经网络",
        "difficulty": "初级",
        "rag_context": [{"id": "c1", "content": "证据"}],
        "feedback": "补充例子",
        "description": "核心概念",
        "learner_profile": "零基础",
        "depth_tier": "beginner",
    }
    old_result = legacy(db, **kwargs)
    message = ResourceGenerationAgentAdapter(db, trace_id="trace-gen").run(
        task_id="task-gen", agent_input=kwargs
    )
    assert message.output == old_result
    assert calls[0] == calls[1]


def test_evaluation_adapter_preserves_legacy_top_level_shape(monkeypatch):
    calls = []

    def legacy(db, user_id):
        calls.append((db, user_id))
        return {
            "overallScore": 72,
            "level": "良好",
            "trend": "improving",
            "dimensions": [],
            "weakPoints": [],
            "summary": "保持进步",
            "suggestions": ["继续练习"],
            "adjustment": {"nextKpId": "nn"},
            "generatedBy": "mock",
            "signals": {"attemptCount": 2},
        }

    monkeypatch.setattr(evaluation_agent, "evaluate", legacy)
    db = object()
    old_result = legacy(db, "u_1")
    message = EvaluationAgentAdapter(db, trace_id="trace-eval").run(
        task_id="task-eval", agent_input={"user_id": "u_1"}
    )
    assert message.output == old_result
    assert "root" not in message.output
    assert calls == [(db, "u_1"), (db, "u_1")]


def test_adapter_inputs_are_strict(monkeypatch):
    monkeypatch.setattr(
        diagnostic_agent,
        "run_diagnostic",
        lambda db, **kwargs: {
            "agentId": "diagnosis",
            "name": "诊断",
            "prompt": "p",
            "output": {},
        },
    )
    with pytest.raises(ValidationError):
        LearningDiagnosisAgentAdapter(object()).run(
            task_id="task-invalid",
            agent_input={"kp_id": "nn", "unexpected": True},
        )


@pytest.mark.parametrize(
    ("passed", "expected"),
    [(True, QualityDecision.PASS), (False, QualityDecision.REVISE)],
)
def test_critic_adapter_maps_legacy_result_to_quality_decision(monkeypatch, passed, expected):
    calls = []

    def legacy(db, **kwargs):
        calls.append((db, kwargs))
        return {
            "agentId": "critic",
            "name": "质量评估Agent",
            "prompt": "critic prompt",
            "output": {
                "passed": passed,
                "validationScore": 0.85,
                "hallucinationRate": 0.05,
                "issues": [] if passed else ["引用不足"],
            },
        }

    monkeypatch.setattr(critic_agent, "run_critic", legacy)
    db = object()
    message = CriticAgentAdapter(db, trace_id="trace-critic").run(
        task_id="task-critic",
        agent_input={
            "learning_goal": "掌握神经网络",
            "knowledge_state": {"rag_context": [{"id": "c1", "content": "证据"}]},
            "resources": [{"output": {"markdown": "# 神经网络"}}],
        },
    )

    assert message.output["decision"] == expected.value
    assert calls == [(db, {"draft_content": "# 神经网络", "rag_context": [{"id": "c1", "content": "证据"}]})]
    assert message.metadata["traceId"] == "trace-critic"
