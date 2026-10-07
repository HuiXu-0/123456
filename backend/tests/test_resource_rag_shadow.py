"""TASK-003-E3/E4-B Resource Agent Trusted RAG shadow tests."""
from __future__ import annotations

import json
import threading

from app.agents.protocol import AgentMessage
from app.agents.resource_rag_shadow import ResourceGenerationRAGShadowAdapter
from app.agents.tools.rag_tool import RAGToolError
from app.agents.tools.registry import RAG_TOOL_NAME, ToolRegistry
from app.rag.protocol import EvidenceItem, RAGResponse, TrustReport


class _LegacyAgent:
    agent_name = "resource_generation"

    def __init__(self, barrier: threading.Barrier | None = None) -> None:
        self.barrier = barrier
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        return AgentMessage(
            task_id=kwargs["task_id"],
            agent_name=self.agent_name,
            output={"markdown": "# Legacy authoritative result"},
            metadata={"traceId": "trace-e3"},
        )


class _RAGToolStub:
    def __init__(self, response, barrier=None, **_kwargs) -> None:
        self.response = response
        self.barrier = barrier
        self.calls = []

    def search(self, query, knowledge_scope=None, top_k=5):
        self.calls.append((query, knowledge_scope, top_k))
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response() -> RAGResponse:
    evidence = [
        EvidenceItem(
            content="Trusted evidence one",
            source={"documentId": "doc-shared"},
            score=0.91,
        ),
        EvidenceItem(
            content="Trusted evidence two",
            source={"documentId": "doc-trusted"},
            score=0.85,
        ),
    ]
    report = TrustReport(
        retrieval_confidence=0.91,
        evidence_count=2,
        source_count=2,
        coverage=1.0,
        reason_codes=["evidence_found"],
    )
    return RAGResponse(
        evidence=evidence,
        confidence=0.91,
        evidence_count=2,
        source_count=2,
        reason_codes=["evidence_found"],
        trust_report=report,
        metadata={
            "traceId": "trace-e3",
            "observability": {"retrievalMs": 1.0},
            "serviceObservability": {"totalMs": 1.2},
        },
    )


def _input():
    return {
        "kp_name": "敏感知识点",
        "difficulty": "初级",
        "description": "敏感查询描述",
        "rag_context": [
            {
                "id": "legacy-1",
                "content": "Legacy private body one",
                "metadata": {"docId": "doc-shared"},
            },
            {
                "id": "legacy-2",
                "content": "Legacy private body two",
                "metadata": {"docId": "doc-legacy"},
            },
        ],
    }


def _registry(factory) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RAG_TOOL_NAME, factory, replace=True)
    return registry


def test_registry_contains_rag_tool_and_supports_extension():
    registry = ToolRegistry()
    assert RAG_TOOL_NAME in registry.available_tools
    registry.register("future_tool", lambda **kwargs: kwargs)
    assert registry.create("future_tool", value=3) == {"value": 3}


def test_shadow_runs_legacy_and_tool_in_parallel_and_keeps_legacy_authoritative():
    barrier = threading.Barrier(2)
    legacy = _LegacyAgent(barrier)
    created = []

    def factory(**kwargs):
        tool = _RAGToolStub(_response(), barrier, **kwargs)
        created.append((tool, kwargs))
        return tool

    result = ResourceGenerationRAGShadowAdapter(
        legacy, user_id="private-user", trace_id="trace-e3", registry=_registry(factory)
    ).run(
        task_id="task-e3",
        agent_input=_input(),
        knowledge_scope={"course": "private-course"},
        top_k=2,
    )

    assert result.legacy_result.output == {"markdown": "# Legacy authoritative result"}
    assert result.tool_failed is False
    assert [event.type for event in result.events] == ["shadow.request", "shadow.result"]
    assert {event.traceId for event in result.events} == {"trace-e3"}
    assert created[0][1] == {"user_id": "private-user", "trace_id": "trace-e3"}
    assert created[0][0].calls[0][2] == 2


def test_shadow_records_standard_result_event_without_content():
    result = ResourceGenerationRAGShadowAdapter(
        _LegacyAgent(),
        user_id="private-user",
        trace_id="trace-e3",
        registry=_registry(lambda **kwargs: _RAGToolStub(_response(), **kwargs)),
    ).run(task_id="task-e3", agent_input=_input())

    event = result.events[-1]
    assert event.type == "shadow.result"
    assert event.evidence_count == 2
    assert event.source_count == 2
    assert event.confidence == 0.91
    assert event.latency is not None and event.latency >= 0
    assert result.total_latency >= 0
    assert result.rag_latency == 1.2
    assert result.tool_latency >= 0
    serialized_events = json.dumps(
        [item.model_dump(mode="json") for item in result.events], ensure_ascii=False
    )
    for forbidden in (
        "敏感知识点",
        "敏感查询描述",
        "Legacy private body",
        "Trusted evidence",
        "private-user",
        "private-course",
    ):
        assert forbidden not in serialized_events


def test_tool_error_is_observed_without_replacing_legacy_result():
    error = RAGToolError("trace-e3")
    result = ResourceGenerationRAGShadowAdapter(
        _LegacyAgent(),
        user_id="user-e3",
        trace_id="trace-e3",
        registry=_registry(lambda **kwargs: _RAGToolStub(error, **kwargs)),
    ).run(task_id="task-e3-error", agent_input=_input())

    assert result.tool_failed is True
    assert result.legacy_result.output["markdown"] == "# Legacy authoritative result"
    assert [event.type for event in result.events] == ["shadow.request", "shadow.error"]
    assert result.events[-1].error_type == "RAGToolError"
    assert result.events[-1].evidence_count is None
    assert result.events[-1].source_count is None
    assert result.events[-1].confidence is None
