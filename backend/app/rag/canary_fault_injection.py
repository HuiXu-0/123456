"""Offline Trusted RAG fault injection and rollback verification (TASK-004-D)."""
from __future__ import annotations

import json
import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.protocol import AgentMessage
from app.agents.resource_rag_shadow import ResourceGenerationRAGShadowAdapter
from app.agents.shadow_metrics import ShadowMetricsCollector
from app.agents.tools.registry import RAG_TOOL_NAME, ToolRegistry
from app.rag.protocol import EvidenceItem, RAGResponse, RetrievalCandidate, TrustReport
from app.rag.rerank_gate import DecisionReranker, OfflineRerankGate
from app.rag.reranker import BaseReranker
from app.rag.shadow_admission import ShadowDeadlineExecutor, ShadowTaskStatus


_SENSITIVE_QUERY = "FAULT-INJECTION-SENSITIVE-QUERY"
_SENSITIVE_EVIDENCE = "FAULT-INJECTION-SENSITIVE-EVIDENCE"
_LEGACY_OUTPUT = "FAULT-INJECTION-LEGACY-AUTHORITATIVE"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RollbackPath(str, Enum):
    LEGACY = "legacy"


class ScenarioStatus(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


class FaultScenarioResult(_StrictModel):
    scenarioId: str
    component: Literal["retriever", "vector_store", "reranker", "llm", "metrics"]
    fault: str
    timeoutKind: Literal["none", "exception", "hard_hang"] = "none"
    status: ScenarioStatus
    legacyPreserved: bool
    trustedIsolated: bool
    structuredReason: str
    recordedErrorType: str | None = None
    rollbackPath: RollbackPath
    metricsIsolated: bool
    contentSafe: bool
    elapsedMs: float = Field(ge=0.0)
    componentFallback: Literal["legacy", "hybrid_then_legacy"] = "legacy"
    limitation: str | None = None
    deadlineEnforced: bool = False
    cancellationRequested: bool = False
    timeoutReason: str | None = None


class FaultInjectionReport(_StrictModel):
    schemaVersion: Literal["trusted-rag-fault-injection-v2"]
    productionMutation: Literal[False]
    legacyAuthority: Literal[True]
    scenarioCount: int = Field(ge=1)
    passCount: int = Field(ge=0)
    blockCount: int = Field(ge=0)
    scenarios: list[FaultScenarioResult]
    finalDecision: ScenarioStatus
    blockReasons: list[str]


class RetrieverFailure(RuntimeError):
    pass


class CollectionUnavailableError(RuntimeError):
    pass


class VectorQueryError(RuntimeError):
    pass


class VectorDataError(RuntimeError):
    pass


class RerankerLoadError(RuntimeError):
    pass


class RerankerInferenceError(RuntimeError):
    pass


class LLMDegradationError(RuntimeError):
    pass


class _LegacyProbeAgent:
    agent_name = "resource_generation"

    def run(self, *, task_id: str, **_: Any) -> AgentMessage:
        return AgentMessage(
            task_id=task_id,
            agent_name=self.agent_name,
            output={"markdown": _LEGACY_OUTPUT},
            metadata={"traceId": "fault-probe"},
        )


class _FaultTool:
    def __init__(self, outcome: Any, *, delay_ms: float = 0.0, **_: Any) -> None:
        self.outcome = outcome
        self.delay_ms = delay_ms

    def search(self, *_: Any, **__: Any) -> Any:
        if self.delay_ms:
            time.sleep(self.delay_ms / 1_000)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FailingMetricsSink:
    def record(self, _observation: Any) -> None:
        raise RuntimeError("SENSITIVE-METRICS-SINK-MESSAGE")


class _FailingReranker(BaseReranker):
    def rerank(self, query: str, candidates: list[RetrievalCandidate]):
        del query, candidates
        raise RerankerInferenceError("SENSITIVE-RERANKER-MESSAGE")


def _registry(outcome: Any, *, delay_ms: float = 0.0) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        RAG_TOOL_NAME,
        lambda **kwargs: _FaultTool(outcome, delay_ms=delay_ms, **kwargs),
        replace=True,
    )
    return registry


def _agent_input() -> dict[str, Any]:
    return {
        "kp_name": _SENSITIVE_QUERY,
        "difficulty": "fault-probe",
        "description": "SENSITIVE-DESCRIPTION",
        "rag_context": [
            {
                "id": "legacy-private",
                "content": "SENSITIVE-LEGACY-EVIDENCE",
                "metadata": {"docId": "legacy-doc"},
            }
        ],
    }


def _valid_response(*, empty: bool = False) -> RAGResponse:
    evidence = [] if empty else [
        EvidenceItem(
            content=_SENSITIVE_EVIDENCE,
            source={"documentId": "trusted-doc"},
            score=0.9,
        )
    ]
    reason_codes = ["NO_EVIDENCE", "LOW_SCORE"] if empty else ["evidence_found"]
    return RAGResponse(
        evidence=evidence,
        confidence=0.0 if empty else 0.9,
        evidence_count=len(evidence),
        source_count=len(evidence),
        reason_codes=reason_codes,
        trust_report=TrustReport(
            retrieval_confidence=0.0 if empty else 0.9,
            evidence_count=len(evidence),
            source_count=len(evidence),
            coverage=0.0 if empty else 1.0,
            reason_codes=reason_codes,
        ),
        metadata={"serviceObservability": {"totalMs": 1.0}},
    )


def _reranker_fallback_verified() -> bool:
    query = "fault-reranker-query"
    candidates = [
        RetrievalCandidate(id="anon-a", content="candidate a", confidence_score=0.9),
        RetrievalCandidate(id="anon-b", content="candidate b", confidence_score=0.8),
    ]
    wrapper = DecisionReranker(
        _FailingReranker(),
        OfflineRerankGate("always"),
        {query: ("概念解释", 0.99)},
    )
    results = wrapper.rerank(query, candidates)
    return (
        [item.candidate_id for item in results] == ["anon-a", "anon-b"]
        and wrapper.decisions[-1].reason == "reranker_error_hybrid_fallback"
    )


class CanaryFaultInjectionEvaluator:
    """Exercise failure semantics without wiring the candidate path to production."""

    def __init__(self, *, hard_timeout_budget_ms: float = 10.0, hang_probe_ms: float = 80.0) -> None:
        if hard_timeout_budget_ms <= 0 or hang_probe_ms <= hard_timeout_budget_ms:
            raise ValueError("hang_probe_ms必须大于正的hard_timeout_budget_ms")
        self.hard_timeout_budget_ms = hard_timeout_budget_ms
        self.hang_probe_ms = hang_probe_ms

    def _run_hard_hang(self) -> FaultScenarioResult:
        """Probe the E2 Shadow-only deadline without invoking a production route."""
        legacy = _LegacyProbeAgent().run(task_id="task-retriever_hard_hang")
        executor = ShadowDeadlineExecutor(max_isolated_workers=1)
        outcome = executor.run(
            lambda _cancellation: _FaultTool(
                _valid_response(), delay_ms=self.hang_probe_ms
            ).search(),
            deadline_ms=self.hard_timeout_budget_ms,
        )
        legacy_preserved = legacy.output.get("markdown") == _LEGACY_OUTPUT
        isolated = (
            outcome.status is ShadowTaskStatus.DEADLINE_EXCEEDED
            and outcome.worker_isolated
            and legacy_preserved
        )
        return FaultScenarioResult(
            scenarioId="retriever_hard_hang",
            component="retriever",
            fault="isolated_hang_probe",
            timeoutKind="hard_hang",
            status=ScenarioStatus.PASS if isolated else ScenarioStatus.BLOCK,
            legacyPreserved=legacy_preserved,
            trustedIsolated=isolated,
            structuredReason=(
                "timeout.hard_hang_deadline_isolated"
                if isolated
                else "timeout.hard_hang_deadline_failed"
            ),
            recordedErrorType="ShadowDeadlineExceeded",
            rollbackPath=RollbackPath.LEGACY,
            metricsIsolated=True,
            contentSafe=True,
            elapsedMs=outcome.elapsed_ms,
            limitation=(
                "Offline Shadow worker uses daemon isolation; cooperative cancellation "
                "cannot forcibly terminate a blocking Python dependency."
            ),
            deadlineEnforced=True,
            cancellationRequested=outcome.cancellation_requested,
            timeoutReason=outcome.timeout_reason,
        )

    def _run_shadow(
        self,
        *,
        scenario_id: str,
        component: str,
        fault: str,
        structured_reason: str,
        outcome: Any,
        timeout_kind: str = "none",
        delay_ms: float = 0.0,
        metrics_sink: Any | None = None,
        empty_is_failure: bool = False,
        expected_block: bool = False,
        component_fallback: str = "legacy",
        limitation: str | None = None,
    ) -> FaultScenarioResult:
        collector = metrics_sink if metrics_sink is not None else ShadowMetricsCollector()
        started = time.perf_counter()
        result = ResourceGenerationRAGShadowAdapter(
            _LegacyProbeAgent(),
            user_id="fault-probe-user",
            trace_id=f"trace-{scenario_id}",
            registry=_registry(outcome, delay_ms=delay_ms),
            metrics_sink=collector,
        ).run(task_id=f"task-{scenario_id}", agent_input=_agent_input())
        elapsed_ms = (time.perf_counter() - started) * 1_000
        legacy_preserved = result.legacy_result.output.get("markdown") == _LEGACY_OUTPUT
        last_event = result.events[-1]
        empty_detected = last_event.type == "shadow.result" and last_event.evidence_count == 0
        failure_detected = result.tool_failed or (empty_is_failure and empty_detected)
        hard_deadline_met = not (
            timeout_kind == "hard_hang" and elapsed_ms > self.hard_timeout_budget_ms
        )
        trusted_isolated = legacy_preserved and failure_detected and hard_deadline_met
        if fault == "metrics_sink_failure":
            trusted_isolated = legacy_preserved and not result.tool_failed
        status = ScenarioStatus.BLOCK if expected_block or not trusted_isolated else ScenarioStatus.PASS
        if fault == "metrics_sink_failure" and trusted_isolated:
            status = ScenarioStatus.PASS
        recorded_error = last_event.error_type if last_event.type == "shadow.error" else None
        safe_payload = json.dumps(
            [event.model_dump(mode="json") for event in result.events], ensure_ascii=False
        )
        content_safe = all(
            forbidden not in safe_payload
            for forbidden in (
                _SENSITIVE_QUERY,
                _SENSITIVE_EVIDENCE,
                "SENSITIVE-DESCRIPTION",
                "SENSITIVE-LEGACY-EVIDENCE",
                "SENSITIVE-METRICS-SINK-MESSAGE",
            )
        )
        return FaultScenarioResult(
            scenarioId=scenario_id,
            component=component,
            fault=fault,
            timeoutKind=timeout_kind,
            status=status,
            legacyPreserved=legacy_preserved,
            trustedIsolated=trusted_isolated,
            structuredReason=structured_reason,
            recordedErrorType=recorded_error,
            rollbackPath=RollbackPath.LEGACY,
            metricsIsolated=(fault == "metrics_sink_failure" and legacy_preserved)
            or fault != "metrics_sink_failure",
            contentSafe=content_safe,
            elapsedMs=round(elapsed_ms, 6),
            componentFallback=component_fallback,
            limitation=limitation,
        )

    def evaluate(self) -> FaultInjectionReport:
        cases = [
            self._run_shadow(
                scenario_id="retriever_timeout_exception",
                component="retriever",
                fault="TimeoutError",
                structured_reason="retriever.timeout_exception",
                outcome=TimeoutError("SENSITIVE-TIMEOUT-MESSAGE"),
                timeout_kind="exception",
            ),
            self._run_shadow(
                scenario_id="retriever_runtime_error",
                component="retriever",
                fault="runtime_error",
                structured_reason="retriever.runtime_error",
                outcome=RetrieverFailure("SENSITIVE-RETRIEVER-MESSAGE"),
            ),
            self._run_shadow(
                scenario_id="retriever_empty_evidence",
                component="retriever",
                fault="empty_evidence",
                structured_reason="retriever.empty_evidence_canary_block",
                outcome=_valid_response(empty=True),
                empty_is_failure=True,
            ),
            self._run_hard_hang(),
            self._run_shadow(
                scenario_id="vector_collection_unavailable",
                component="vector_store",
                fault="collection_unavailable",
                structured_reason="vector_store.collection_unavailable",
                outcome=CollectionUnavailableError("SENSITIVE-COLLECTION-MESSAGE"),
            ),
            self._run_shadow(
                scenario_id="vector_query_error",
                component="vector_store",
                fault="query_error",
                structured_reason="vector_store.query_error",
                outcome=VectorQueryError("SENSITIVE-VECTOR-QUERY-MESSAGE"),
            ),
            self._run_shadow(
                scenario_id="vector_data_error",
                component="vector_store",
                fault="invalid_data",
                structured_reason="vector_store.invalid_data",
                outcome=VectorDataError("SENSITIVE-VECTOR-DATA-MESSAGE"),
            ),
            self._run_shadow(
                scenario_id="reranker_load_failure",
                component="reranker",
                fault="model_load_failure",
                structured_reason="reranker.model_load_failure",
                outcome=RerankerLoadError("SENSITIVE-RERANKER-LOAD-MESSAGE"),
            ),
            self._run_shadow(
                scenario_id="reranker_inference_failure",
                component="reranker",
                fault="model_inference_failure",
                structured_reason="reranker.inference_hybrid_fallback_canary_block",
                outcome=RerankerInferenceError("SENSITIVE-RERANKER-INFERENCE-MESSAGE"),
                component_fallback="hybrid_then_legacy",
                limitation=(
                    "DecisionReranker fallback verification="
                    f"{str(_reranker_fallback_verified()).lower()}"
                ),
            ),
            self._run_shadow(
                scenario_id="llm_controlled_degradation",
                component="llm",
                fault="llm_failure",
                structured_reason="llm.candidate_failure_legacy_fallback",
                outcome=LLMDegradationError("SENSITIVE-LLM-MESSAGE"),
                limitation=(
                    "Trusted RAG Service itself has no LLM call; this probes the isolated "
                    "candidate-generation boundary only."
                ),
            ),
            self._run_shadow(
                scenario_id="metrics_sink_failure",
                component="metrics",
                fault="metrics_sink_failure",
                structured_reason="metrics.sink_failure_isolated",
                outcome=_valid_response(),
                metrics_sink=_FailingMetricsSink(),
            ),
        ]
        block_reasons = [item.structuredReason for item in cases if item.status is ScenarioStatus.BLOCK]
        block_count = len(block_reasons)
        return FaultInjectionReport(
            schemaVersion="trusted-rag-fault-injection-v2",
            productionMutation=False,
            legacyAuthority=True,
            scenarioCount=len(cases),
            passCount=len(cases) - block_count,
            blockCount=block_count,
            scenarios=cases,
            finalDecision=ScenarioStatus.BLOCK if block_count else ScenarioStatus.PASS,
            blockReasons=block_reasons,
        )
