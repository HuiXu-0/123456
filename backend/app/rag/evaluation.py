"""离线检索指标、评测执行与新旧影子对比（TASK-003-C1）。"""
from __future__ import annotations

import json
import math
import time
import statistics
from pathlib import Path
from typing import Any

from app.rag.evaluation_protocol import (
    RetrievalEvaluationCase,
    RetrievalEvaluationReport,
    RetrievalEvaluationResult,
)
from app.rag.pipeline import TrustedRetrievalPipeline
from app.rag.protocol import RAGRequest


def load_evaluation_cases(path: str | Path) -> list[RetrievalEvaluationCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RetrievalEvaluationCase.model_validate(item) for item in payload]


def validate_evaluation_dataset(
    cases: list[RetrievalEvaluationCase],
    *,
    available_document_ids: set[str] | None = None,
    available_chunk_ids: set[str] | None = None,
) -> None:
    case_ids: set[str] = set()
    queries: dict[str, tuple[set[str], set[str]]] = {}
    for case in cases:
        if case.case_id in case_ids:
            raise ValueError(f"重复case_id：{case.case_id}")
        case_ids.add(case.case_id)
        normalized_query = " ".join(case.query.lower().split())
        expectation = (set(case.expected_document_ids), set(case.expected_chunk_ids))
        if normalized_query in queries:
            if queries[normalized_query] != expectation:
                raise ValueError(f"相同query存在冲突标注：{case.query}")
            raise ValueError(f"重复query：{case.query}")
        queries[normalized_query] = expectation
        if available_document_ids is not None:
            invalid = set(case.expected_document_ids) - available_document_ids
            invalid.update(
                expected.document_id
                for expected in case.expected_evidence
                if expected.document_id and expected.document_id not in available_document_ids
            )
            if invalid:
                raise ValueError(f"{case.case_id}包含失效document ID：{sorted(invalid)}")
        if available_chunk_ids is not None:
            invalid_chunks = set(case.expected_chunk_ids) - available_chunk_ids
            invalid_chunks.update(
                expected.chunk_id
                for expected in case.expected_evidence
                if expected.chunk_id and expected.chunk_id not in available_chunk_ids
            )
            if invalid_chunks:
                raise ValueError(f"{case.case_id}包含失效chunk ID：{sorted(invalid_chunks)}")


def _identity(item: dict[str, Any]) -> tuple[str | None, str | None]:
    metadata = item.get("metadata") or {}
    source = item.get("source") or {}
    chunk_id = item.get("id") or source.get("chunkId")
    document_id = (
        metadata.get("document_id")
        or metadata.get("docId")
        or source.get("documentId")
    )
    return document_id, chunk_id


def _relevance(case: RetrievalEvaluationCase) -> dict[str, int]:
    grades: dict[str, int] = {}
    if case.expected_evidence:
        for expected in case.expected_evidence:
            if expected.document_id:
                grades[f"doc:{expected.document_id}"] = expected.relevance
            if expected.chunk_id:
                grades[f"chunk:{expected.chunk_id}"] = expected.relevance
    elif case.expected_chunk_ids:
        for chunk_id in case.expected_chunk_ids:
            grades[f"chunk:{chunk_id}"] = case.relevance
    else:
        for document_id in case.expected_document_ids:
            grades[f"doc:{document_id}"] = case.relevance
    return grades


def _grade(grades: dict[str, int], document_id: str | None, chunk_id: str | None) -> int:
    return max(
        grades.get(f"doc:{document_id}", 0) if document_id else 0,
        grades.get(f"chunk:{chunk_id}", 0) if chunk_id else 0,
    )


def calculate_case_metrics(
    case: RetrievalEvaluationCase,
    items: list[dict[str, Any]],
    *,
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, float | None]:
    if case.relevance == 0:
        metrics: dict[str, float | None] = {
            f"recall@{k}": None for k in ks
        }
        metrics.update({f"hit_rate@{k}": None for k in ks})
        metrics.update({f"ndcg@{k}": None for k in ks})
        metrics["mrr"] = None
        return metrics

    grades = _relevance(case)
    relevant_keys = set(grades)
    ranked = [_identity(item) for item in items]

    def matched_keys(prefix: list[tuple[str | None, str | None]]) -> set[str]:
        found: set[str] = set()
        for document_id, chunk_id in prefix:
            if document_id and f"doc:{document_id}" in grades:
                found.add(f"doc:{document_id}")
            if chunk_id and f"chunk:{chunk_id}" in grades:
                found.add(f"chunk:{chunk_id}")
        return found

    metrics = {}
    for k in ks:
        found = matched_keys(ranked[:k])
        metrics[f"recall@{k}"] = len(found) / len(relevant_keys)
        metrics[f"hit_rate@{k}"] = 1.0 if found else 0.0
        gains = []
        credited: set[str] = set()
        for document_id, chunk_id in ranked[:k]:
            matching = set()
            if document_id and f"doc:{document_id}" in grades:
                matching.add(f"doc:{document_id}")
            if chunk_id and f"chunk:{chunk_id}" in grades:
                matching.add(f"chunk:{chunk_id}")
            fresh = matching - credited
            gains.append(max((grades[key] for key in fresh), default=0))
            credited.update(fresh)
        dcg = sum((2**gain - 1) / math.log2(rank + 2) for rank, gain in enumerate(gains))
        ideal = sorted(grades.values(), reverse=True)[:k]
        idcg = sum((2**gain - 1) / math.log2(rank + 2) for rank, gain in enumerate(ideal))
        metrics[f"ndcg@{k}"] = dcg / idcg if idcg else None
    first = next(
        (rank for rank, (doc, chunk) in enumerate(ranked, start=1) if _grade(grades, doc, chunk)),
        None,
    )
    metrics["mrr"] = 1.0 / first if first else 0.0
    return metrics


def source_completeness(items: list[dict[str, Any]]) -> float:
    if not items:
        # 空结果质量由EmptyResultRate单独衡量；没有返回项也就没有缺失来源字段。
        return 1.0
    complete = 0
    for item in items:
        metadata = item.get("metadata") or {}
        source = item.get("source") or {}
        document_id = metadata.get("document_id") or metadata.get("docId") or source.get("documentId")
        locator = (
            metadata.get("document_title")
            or metadata.get("title")
            or metadata.get("source_location")
            or source.get("title")
            or source.get("location")
        )
        complete += bool(document_id and locator)
    return complete / len(items)


def source_coverage(items: list[dict[str, Any]]) -> float:
    """返回结果中的来源多样性：唯一文档数/结果数。"""
    if not items:
        return 1.0
    documents = {document_id for document_id, _ in map(_identity, items) if document_id}
    return len(documents) / len(items)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def measure_latency_phases(
    old_call,
    new_call,
    *,
    warmup_rounds: int = 2,
    steady_rounds: int = 5,
) -> dict[str, Any]:
    """分离冷启动/预热/稳态，并在稳态交替新旧执行顺序。"""
    if warmup_rounds < 0 or steady_rounds < 1:
        raise ValueError("warmup_rounds需>=0且steady_rounds需>=1")

    def measured(call) -> float:
        started = time.perf_counter()
        call()
        return (time.perf_counter() - started) * 1000

    cold = {"oldMs": measured(old_call), "newMs": measured(new_call)}
    warmup_order = []
    for index in range(warmup_rounds):
        order = (("old", old_call), ("new", new_call)) if index % 2 == 0 else (("new", new_call), ("old", old_call))
        warmup_order.append([name for name, _ in order])
        for _, call in order:
            call()

    samples = {"old": [], "new": []}
    execution_order = []
    for index in range(steady_rounds):
        order = (("old", old_call), ("new", new_call)) if index % 2 == 0 else (("new", new_call), ("old", old_call))
        execution_order.append([name for name, _ in order])
        for name, call in order:
            samples[name].append(measured(call))

    def summary(values: list[float]) -> dict[str, float | int]:
        return {
            "sampleCount": len(values),
            "meanMs": sum(values) / len(values),
            "medianMs": statistics.median(values),
            "p95Ms": _percentile(values, 0.95) or 0.0,
        }

    return {
        "coldStart": cold,
        "warmupRounds": warmup_rounds,
        "warmupOrder": warmup_order,
        "steadyRounds": steady_rounds,
        "executionOrder": execution_order,
        "steadyState": {"old": summary(samples["old"]), "new": summary(samples["new"])},
    }


def _candidate_score(item: dict[str, Any]) -> float:
    for key in ("score", "rrfScore", "vectorScore"):
        if item.get(key) is not None:
            return float(item[key])
    return 0.0


class RetrievalEvaluator:
    def __init__(
        self,
        old_retriever: Any,
        pipeline: TrustedRetrievalPipeline,
        *,
        old_system_name: str = "hybrid_retriever",
        new_system_name: str = "trusted_pipeline",
    ) -> None:
        self.old_retriever = old_retriever
        self.pipeline = pipeline
        self.old_system_name = old_system_name
        self.new_system_name = new_system_name

    def _run_old(self, case: RetrievalEvaluationCase) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        if isinstance(self.old_retriever, TrustedRetrievalPipeline):
            response = self.old_retriever.execute(
                RAGRequest(
                    query=case.query,
                    user_id="offline-evaluation",
                    knowledge_scope=case.knowledge_scope,
                    top_k=5,
                )
            )
            items = [item.model_dump(mode="json") for item in response.evidence]
        else:
            items = self.old_retriever.search(case.query, top_k=5)
        return items, (time.perf_counter() - started) * 1000

    def _run_new(self, case: RetrievalEvaluationCase) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        response = self.pipeline.execute(
            RAGRequest(
                query=case.query,
                user_id="offline-evaluation",
                knowledge_scope=case.knowledge_scope,
                top_k=5,
            )
        )
        items = [item.model_dump(mode="json") for item in response.evidence]
        return items, (time.perf_counter() - started) * 1000

    @staticmethod
    def _result(case, system, items, latency) -> RetrievalEvaluationResult:
        identities = [_identity(item) for item in items]
        return RetrievalEvaluationResult(
            case_id=case.case_id,
            system=system,
            retrieved_document_ids=[doc for doc, _ in identities if doc],
            retrieved_chunk_ids=[chunk for _, chunk in identities if chunk],
            scores=[_candidate_score(item) for item in items],
            metrics=calculate_case_metrics(case, items),
            latency_ms=latency,
            empty_result=not items,
            source_completeness=source_completeness(items),
            source_coverage=source_coverage(items),
        )

    @staticmethod
    def shadow_compare(
        case_id: str,
        old_items: list[dict[str, Any]],
        new_items: list[dict[str, Any]],
        old_latency_ms: float,
        new_latency_ms: float,
        *,
        k: int = 5,
    ) -> dict[str, Any]:
        old_ids = [_identity(item) for item in old_items[:k]]
        new_ids = [_identity(item) for item in new_items[:k]]
        old_docs = [doc for doc, _ in old_ids if doc]
        new_docs = [doc for doc, _ in new_ids if doc]
        old_chunks = [chunk for _, chunk in old_ids if chunk]
        new_chunks = [chunk for _, chunk in new_ids if chunk]
        common = set(old_chunks) & set(new_chunks)
        old_rank = {chunk: rank for rank, chunk in enumerate(old_chunks, start=1)}
        new_rank = {chunk: rank for rank, chunk in enumerate(new_chunks, start=1)}
        old_by_chunk = {chunk: item for item, (_, chunk) in zip(old_items, old_ids) if chunk}
        new_by_chunk = {chunk: item for item, (_, chunk) in zip(new_items, new_ids) if chunk}
        return {
            "caseId": case_id,
            "overlapAtK": len(common) / max(1, min(k, len(set(old_chunks) | set(new_chunks)))),
            "oldOnlyDocuments": sorted(set(old_docs) - set(new_docs)),
            "newOnlyDocuments": sorted(set(new_docs) - set(old_docs)),
            "rankingDifferences": {chunk: new_rank[chunk] - old_rank[chunk] for chunk in sorted(common)},
            "scoreDifferences": {
                chunk: round(_candidate_score(new_by_chunk[chunk]) - _candidate_score(old_by_chunk[chunk]), 6)
                for chunk in sorted(common)
            },
            "sourceFieldDifferences": {
                chunk: {
                    "old": source_completeness([old_by_chunk[chunk]]),
                    "new": source_completeness([new_by_chunk[chunk]]),
                }
                for chunk in sorted(common)
                if source_completeness([old_by_chunk[chunk]]) != source_completeness([new_by_chunk[chunk]])
            },
            "latencyDifferenceMs": round(new_latency_ms - old_latency_ms, 4),
        }

    def evaluate(
        self,
        cases: list[RetrievalEvaluationCase],
        *,
        dataset_name: str = "trusted-rag-baseline-v1",
    ) -> RetrievalEvaluationReport:
        results: list[RetrievalEvaluationResult] = []
        differences = []
        for case in cases:
            old_items, old_latency = self._run_old(case)
            new_items, new_latency = self._run_new(case)
            results.extend(
                [
                    self._result(case, self.old_system_name, old_items, old_latency),
                    self._result(case, self.new_system_name, new_items, new_latency),
                ]
            )
            differences.append(
                self.shadow_compare(case.case_id, old_items, new_items, old_latency, new_latency)
            )

        summaries: dict[str, dict[str, float | None]] = {}
        for system in (self.old_system_name, self.new_system_name):
            rows = [result for result in results if result.system == system]
            metric_names = sorted({name for row in rows for name in row.metrics})
            summary: dict[str, float | None] = {}
            for name in metric_names:
                values = [row.metrics[name] for row in rows if row.metrics.get(name) is not None]
                summary[name] = sum(values) / len(values) if values else None
            summary["empty_result_rate"] = sum(row.empty_result for row in rows) / len(rows) if rows else None
            summary["source_completeness"] = sum(row.source_completeness for row in rows) / len(rows) if rows else None
            summary["source_coverage"] = sum(row.source_coverage for row in rows) / len(rows) if rows else None
            confidence_values = [score for row in rows for score in row.scores]
            summary["confidence_min"] = min(confidence_values) if confidence_values else None
            summary["confidence_mean"] = (
                sum(confidence_values) / len(confidence_values)
                if confidence_values
                else None
            )
            summary["confidence_p50"] = _percentile(confidence_values, 0.50)
            summary["confidence_p95"] = _percentile(confidence_values, 0.95)
            summary["confidence_max"] = max(confidence_values) if confidence_values else None
            latencies = [row.latency_ms for row in rows]
            summary["latency_p50_ms"] = _percentile(latencies, 0.50)
            summary["latency_p95_ms"] = _percentile(latencies, 0.95)
            summaries[system] = summary
        return RetrievalEvaluationReport(
            dataset_name=dataset_name,
            case_count=len(cases),
            systems=summaries,
            results=results,
            shadow_differences=differences,
            limitations=[
                "无答案用例的Recall、HitRate、MRR和nDCG标记为不可计算",
                "当前知识库规模较小，延迟只代表本地单进程基线",
            ],
        )
