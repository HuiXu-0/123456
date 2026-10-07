"""Resource Agent Trusted RAG shadow adapter and events (TASK-003-E4-B).

> **R1 归属说明（不改逻辑）**：本模块是 Trusted RAG **影子评估**路径，仅被
> `app/rag/canary_fault_injection.py`（评估脚本）与 `tests/` 引用，**不在生产
> 生成链路**。它已消费 `agents.tools.ToolRegistry`，但那是实验侧而非业务侧。
> R1 起 Agent 目录以 `app.agents.registry` 为单一真值源，本模块不注册为业务 Agent。
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.adapters import ResourceGenerationInput
from app.agents.protocol import AgentMessage
from app.agents.shadow_metrics import (
    ShadowMetricObservation,
    ShadowMetricsSink,
)
from app.agents.state import AgentState
from app.agents.tools.rag_tool import RAGToolError
from app.agents.tools.registry import RAG_TOOL_NAME, ToolRegistry, get_tool_registry
from app.rag.protocol import RAGResponse

logger = logging.getLogger("app.agents.resource_rag_shadow")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResultDifference(_StrictModel):
    legacy_evidence_count: int = Field(ge=0)
    trusted_evidence_count: int = Field(ge=0)
    evidence_count_delta: int
    shared_reference_count: int = Field(ge=0)
    legacy_only_count: int = Field(ge=0)
    trusted_only_count: int = Field(ge=0)
    reference_jaccard: float = Field(ge=0.0, le=1.0)


class ShadowEvent(_StrictModel):
    """Standard content-free event safe for logs and audit streams."""

    type: Literal["shadow.request", "shadow.result", "shadow.error"]
    traceId: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    latency: float | None = Field(default=None, ge=0.0)
    evidence_count: int | None = Field(default=None, ge=0)
    source_count: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    error_type: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_event_shape(self) -> "ShadowEvent":
        result_fields = (
            self.latency,
            self.evidence_count,
            self.source_count,
            self.confidence,
        )
        if self.type == "shadow.request" and (
            any(value is not None for value in result_fields) or self.error_type is not None
        ):
            raise ValueError("shadow.request must not contain result or error fields")
        if self.type == "shadow.result" and (
            any(value is None for value in result_fields) or self.error_type is not None
        ):
            raise ValueError("shadow.result requires metric fields and no error_type")
        if self.type == "shadow.error" and (
            self.latency is None
            or self.error_type is None
            or any(value is not None for value in result_fields[1:])
        ):
            raise ValueError("shadow.error requires latency/error_type only")
        return self


class ResourceRAGShadowResult(_StrictModel):
    """Legacy output remains authoritative; shadow data is observational only."""

    legacy_result: AgentMessage
    events: list[ShadowEvent]
    total_latency: float = Field(ge=0.0)
    rag_latency: float | None = Field(default=None, ge=0.0)
    tool_latency: float = Field(ge=0.0)
    tool_failed: bool = False


class LegacyResourceAgent(Protocol):
    agent_name: str

    def run(
        self,
        *,
        task_id: str,
        agent_input: ResourceGenerationInput,
        state: AgentState | dict[str, Any] | None = None,
    ) -> AgentMessage: ...


class ResourceGenerationRAGShadowAdapter:
    """Run Legacy generation and Trusted RAG concurrently without cutover."""

    def __init__(
        self,
        legacy_agent: LegacyResourceAgent,
        *,
        user_id: str,
        trace_id: str,
        registry: ToolRegistry | None = None,
        metrics_sink: ShadowMetricsSink | None = None,
    ) -> None:
        self.legacy_agent = legacy_agent
        self.user_id = user_id
        self.trace_id = trace_id
        self.registry = registry or get_tool_registry()
        self.metrics_sink = metrics_sink

    @staticmethod
    def _legacy_references(agent_input: ResourceGenerationInput) -> set[str]:
        references: set[str] = set()
        for item in agent_input.rag_context:
            metadata = item.get("metadata") if isinstance(item, dict) else None
            metadata = metadata if isinstance(metadata, dict) else {}
            reference = (
                metadata.get("document_id")
                or metadata.get("docId")
                or item.get("documentId")
                or item.get("id")
            )
            if reference is not None:
                references.add(str(reference))
        return references

    @staticmethod
    def _trusted_references(response: RAGResponse) -> set[str]:
        references: set[str] = set()
        for evidence in response.evidence:
            reference = evidence.source.get("documentId") or evidence.source.get("chunkId")
            if reference is not None:
                references.add(str(reference))
        return references

    @classmethod
    def _difference(
        cls, agent_input: ResourceGenerationInput, response: RAGResponse
    ) -> ResultDifference:
        legacy = cls._legacy_references(agent_input)
        trusted = cls._trusted_references(response)
        union = legacy | trusted
        shared = legacy & trusted
        return ResultDifference(
            legacy_evidence_count=len(agent_input.rag_context),
            trusted_evidence_count=response.evidence_count,
            evidence_count_delta=response.evidence_count - len(agent_input.rag_context),
            shared_reference_count=len(shared),
            legacy_only_count=len(legacy - trusted),
            trusted_only_count=len(trusted - legacy),
            reference_jaccard=(len(shared) / len(union)) if union else 1.0,
        )

    @staticmethod
    def _timed(call):
        started = time.perf_counter()
        try:
            return call(), (time.perf_counter() - started) * 1000, None
        except Exception as exc:  # result is inspected and handled by the caller
            return None, (time.perf_counter() - started) * 1000, exc

    @staticmethod
    def _error_type(error: Exception) -> str:
        current = error
        visited: set[int] = set()
        while current.__cause__ is not None and id(current) not in visited:
            visited.add(id(current))
            current = current.__cause__
        return type(current).__name__

    @staticmethod
    def _rag_latency(response: RAGResponse) -> float | None:
        for metadata_key in ("serviceObservability", "observability"):
            timings = response.metadata.get(metadata_key)
            if isinstance(timings, dict):
                total = timings.get("totalMs")
                if isinstance(total, (int, float)) and total >= 0:
                    return float(total)
        return None

    def _record(self, observation: ShadowMetricObservation) -> None:
        if self.metrics_sink is None:
            return
        try:
            self.metrics_sink.record(observation)
        except Exception as exc:
            logger.error(
                "shadow_metric_record_failed traceId=%s errorType=%s",
                self.trace_id,
                type(exc).__name__,
            )

    def run(
        self,
        *,
        task_id: str,
        agent_input: ResourceGenerationInput | dict[str, Any],
        state: AgentState | dict[str, Any] | None = None,
        knowledge_scope: str | list[str] | dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> ResourceRAGShadowResult:
        total_started = time.perf_counter()
        validated_input = ResourceGenerationInput.model_validate(agent_input)
        query = " ".join(
            part
            for part in (
                validated_input.kp_name,
                validated_input.description,
                validated_input.difficulty,
            )
            if part
        )
        events = [
            ShadowEvent(
                type="shadow.request",
                traceId=self.trace_id,
                agent=self.legacy_agent.agent_name,
                tool=RAG_TOOL_NAME,
            )
        ]

        def run_shadow_tool():
            # Construct the Tool in its own worker. Session-bound dependencies must
            # never be created in or inherited from the Legacy/request thread.
            tool = self.registry.create_rag_tool(
                user_id=self.user_id, trace_id=self.trace_id
            )
            response = tool.search(query, knowledge_scope, top_k)
            if not isinstance(response, RAGResponse) or response.trust_report is None:
                raise RAGToolError(self.trace_id)
            return response

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="resource-rag-shadow") as pool:
            legacy_future = pool.submit(
                self._timed,
                lambda: self.legacy_agent.run(
                    task_id=task_id, agent_input=validated_input, state=state
                ),
            )
            tool_future = pool.submit(
                self._timed,
                run_shadow_tool,
            )
            legacy_result, _legacy_ms, legacy_error = legacy_future.result()
            tool_result, tool_ms, tool_error = tool_future.result()

        if legacy_error is not None:
            raise legacy_error
        if not isinstance(legacy_result, AgentMessage):
            raise TypeError("legacy Resource Agent returned an invalid result")

        if tool_error is not None:
            total_ms = (time.perf_counter() - total_started) * 1000
            error_type = self._error_type(tool_error)
            events.append(
                ShadowEvent(
                    type="shadow.error",
                    traceId=self.trace_id,
                    agent=self.legacy_agent.agent_name,
                    tool=RAG_TOOL_NAME,
                    latency=round(tool_ms, 4),
                    error_type=error_type,
                )
            )
            result = ResourceRAGShadowResult(
                legacy_result=legacy_result,
                events=events,
                total_latency=round(total_ms, 4),
                tool_latency=round(tool_ms, 4),
                tool_failed=True,
            )
            self._record(
                ShadowMetricObservation(
                    traceId=self.trace_id,
                    agent=self.legacy_agent.agent_name,
                    tool=RAG_TOOL_NAME,
                    total_latency=result.total_latency,
                    tool_latency=result.tool_latency,
                    timed_out="timeout" in error_type.lower(),
                    error_type=error_type,
                )
            )
            return result

        # run_shadow_tool validates this before the Future succeeds.
        assert isinstance(tool_result, RAGResponse) and tool_result.trust_report is not None
        difference = self._difference(validated_input, tool_result)
        rag_latency = self._rag_latency(tool_result)
        total_ms = (time.perf_counter() - total_started) * 1000
        events.append(
            ShadowEvent(
                type="shadow.result",
                traceId=self.trace_id,
                agent=self.legacy_agent.agent_name,
                tool=RAG_TOOL_NAME,
                latency=round(tool_ms, 4),
                evidence_count=tool_result.evidence_count,
                source_count=tool_result.source_count,
                confidence=tool_result.confidence,
            )
        )
        result = ResourceRAGShadowResult(
            legacy_result=legacy_result,
            events=events,
            total_latency=round(total_ms, 4),
            rag_latency=round(rag_latency, 4) if rag_latency is not None else None,
            tool_latency=round(tool_ms, 4),
        )
        report = tool_result.trust_report
        reason_codes = list(dict.fromkeys(tool_result.reason_codes + report.reason_codes))
        self._record(
            ShadowMetricObservation(
                traceId=self.trace_id,
                agent=self.legacy_agent.agent_name,
                tool=RAG_TOOL_NAME,
                total_latency=result.total_latency,
                rag_latency=result.rag_latency,
                tool_latency=result.tool_latency,
                evidence_overlap=difference.reference_jaccard,
                source_coverage=report.coverage,
                confidence=result.events[-1].confidence,
                reason_codes=reason_codes,
            )
        )
        return result
