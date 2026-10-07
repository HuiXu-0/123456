"""TASK-003-E4-B Agent-RAG Shadow metric and Session isolation tests."""
from __future__ import annotations

import json
import threading

import pytest
from pydantic import ValidationError

from app.agents.resource_rag_shadow import (
    ResourceGenerationRAGShadowAdapter,
    ShadowEvent,
)
from app.agents.shadow_metrics import ShadowMetricObservation, ShadowMetricsCollector
from app.agents.tools.registry import RAG_TOOL_NAME, ToolRegistry
from app.core.database import SessionLocal
from tests.test_resource_rag_shadow import _LegacyAgent, _RAGToolStub, _input, _response


def _registry(factory) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RAG_TOOL_NAME, factory, replace=True)
    return registry


def test_collector_aggregates_latency_rates_and_quality_metrics():
    collector = ShadowMetricsCollector()
    collector.record(
        ShadowMetricObservation(
            traceId="trace-1", agent="resource", tool="trusted_rag",
            total_latency=10, rag_latency=5, tool_latency=8,
            evidence_overlap=0.5, source_coverage=1.0, confidence=0.4,
            reason_codes=["grounded"],
        )
    )
    collector.record(
        ShadowMetricObservation(
            traceId="trace-2", agent="resource", tool="trusted_rag",
            total_latency=20, rag_latency=15, tool_latency=18,
            evidence_overlap=0.75, source_coverage=0.5, confidence=0.7,
            reason_codes=["grounded", "partial_coverage"],
        )
    )
    collector.record(
        ShadowMetricObservation(
            traceId="trace-3", agent="resource", tool="trusted_rag",
            total_latency=30, rag_latency=25, tool_latency=28,
            evidence_overlap=1.0, source_coverage=0.75, confidence=0.9,
            reason_codes=["grounded"],
        )
    )
    collector.record(
        ShadowMetricObservation(
            traceId="trace-4", agent="resource", tool="trusted_rag",
            total_latency=40, tool_latency=38, timed_out=True,
            error_type="TimeoutError",
        )
    )

    report = collector.report()
    assert report.sample_count == 4
    assert report.success_count == 3
    assert report.performance.total_latency.p50 == 20
    assert report.performance.total_latency.p95 == 40
    assert report.performance.rag_latency.p50 == 15
    assert report.performance.tool_latency.p95 == 38
    assert report.performance.timeout_rate == 0.25
    assert report.performance.error_rate == 0.25
    assert report.quality.evidence_overlap.mean == 0.75
    assert report.quality.source_coverage.mean == 0.75
    assert report.quality.confidence_distribution == {
        "low_0_0.5": 1,
        "medium_0.5_0.8": 1,
        "high_0.8_1.0": 1,
    }
    assert report.quality.reason_codes == {"grounded": 3, "partial_coverage": 1}


def test_adapter_collects_metric_only_observation_and_keeps_legacy_authoritative():
    collector = ShadowMetricsCollector()
    result = ResourceGenerationRAGShadowAdapter(
        _LegacyAgent(),
        user_id="private-user",
        trace_id="trace-metrics",
        registry=_registry(lambda **kwargs: _RAGToolStub(_response(), **kwargs)),
        metrics_sink=collector,
    ).run(task_id="task-metrics", agent_input=_input())

    assert result.legacy_result.output["markdown"] == "# Legacy authoritative result"
    observation = collector.observations()[0]
    assert observation.rag_latency == 1.2
    assert observation.evidence_overlap == pytest.approx(1 / 3)
    assert observation.source_coverage == 1.0
    assert observation.confidence == 0.91
    assert observation.reason_codes == ["evidence_found"]
    serialized = json.dumps(observation.model_dump(mode="json"), ensure_ascii=False)
    for forbidden in (
        "敏感知识点", "敏感查询描述", "Legacy private body",
        "Trusted evidence", "private-user",
    ):
        assert forbidden not in serialized


def test_shadow_tool_and_session_bound_factory_are_isolated_from_legacy_thread():
    barrier = threading.Barrier(2)
    legacy_session = SessionLocal()
    shadow_sessions = []
    observed: dict[str, object] = {}

    class SessionAwareLegacy(_LegacyAgent):
        def run(self, **kwargs):
            observed["legacy_thread"] = threading.get_ident()
            observed["legacy_session"] = kwargs["state"]["db_session"]
            return super().run(**kwargs)

    class SessionAwareTool(_RAGToolStub):
        def __init__(self, *args, db_session, **kwargs):
            super().__init__(*args, **kwargs)
            self.db_session = db_session

        def search(self, *args, **kwargs):
            observed["tool_thread"] = threading.get_ident()
            observed["shadow_session"] = self.db_session
            return super().search(*args, **kwargs)

    def factory(**kwargs):
        observed["factory_thread"] = threading.get_ident()
        shadow_session = SessionLocal()
        shadow_sessions.append(shadow_session)
        return SessionAwareTool(
            _response(), barrier, db_session=shadow_session, **kwargs
        )

    try:
        result = ResourceGenerationRAGShadowAdapter(
            SessionAwareLegacy(barrier),
            user_id="user-isolation",
            trace_id="trace-isolation",
            registry=_registry(factory),
        ).run(
            task_id="task-isolation",
            agent_input=_input(),
            state={"db_session": legacy_session},
        )

        assert result.legacy_result.output["markdown"] == "# Legacy authoritative result"
        assert observed["factory_thread"] == observed["tool_thread"]
        assert observed["legacy_thread"] != observed["tool_thread"]
        assert observed["legacy_session"] is legacy_session
        assert observed["shadow_session"] is shadow_sessions[0]
        assert observed["shadow_session"] is not observed["legacy_session"]
    finally:
        legacy_session.close()
        for session in shadow_sessions:
            session.close()


def test_metric_sink_failure_never_changes_legacy_result():
    class FailingSink:
        def record(self, _observation):
            raise RuntimeError("collector unavailable")

    result = ResourceGenerationRAGShadowAdapter(
        _LegacyAgent(),
        user_id="user-sink-failure",
        trace_id="trace-sink-failure",
        registry=_registry(lambda **kwargs: _RAGToolStub(_response(), **kwargs)),
        metrics_sink=FailingSink(),
    ).run(task_id="task-sink-failure", agent_input=_input())

    assert result.legacy_result.output["markdown"] == "# Legacy authoritative result"
    assert result.tool_failed is False


def test_timeout_is_counted_and_never_replaces_legacy_result():
    collector = ShadowMetricsCollector()
    result = ResourceGenerationRAGShadowAdapter(
        _LegacyAgent(),
        user_id="user-timeout",
        trace_id="trace-timeout",
        registry=_registry(lambda **kwargs: _RAGToolStub(TimeoutError(), **kwargs)),
        metrics_sink=collector,
    ).run(task_id="task-timeout", agent_input=_input())

    assert result.legacy_result.output["markdown"] == "# Legacy authoritative result"
    assert result.tool_failed is True
    assert result.events[-1].type == "shadow.error"
    assert result.events[-1].error_type == "TimeoutError"
    report = collector.report()
    assert report.performance.timeout_rate == 1.0
    assert report.performance.error_rate == 1.0


def test_standard_event_rejects_content_and_invalid_shapes():
    with pytest.raises(ValidationError):
        ShadowEvent(
            type="shadow.request",
            traceId="trace-safe",
            agent="resource",
            tool="trusted_rag",
            query="forbidden query body",
        )
    with pytest.raises(ValidationError):
        ShadowEvent(
            type="shadow.result",
            traceId="trace-safe",
            agent="resource",
            tool="trusted_rag",
            latency=1.0,
        )


def test_collector_is_bounded():
    collector = ShadowMetricsCollector(max_samples=2)
    for index in range(3):
        collector.record(
            ShadowMetricObservation(
                traceId=f"trace-{index}", agent="resource", tool="trusted_rag",
                total_latency=index, tool_latency=index,
            )
        )
    assert [item.traceId for item in collector.observations()] == ["trace-1", "trace-2"]
