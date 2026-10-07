"""重排（B3，需求文档 4.3.3：bge-reranker-base CrossEncoder 二次打分）。

降级约定（任务硬性要求 + 接口文档 14.4）：当 bge-reranker 加载失败 / 超时 / 推理异常时，
`rerank()` 返回 `used=False`，候选退化为仅按 RRF 分数排序（归一化到 0-1），并打 **WARNING**
日志；接口仍正常返回 code 0。模型延迟加载（首次 rerank 时才加载），不阻塞 uvicorn 启动。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import math
from pathlib import Path
import threading
import time
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.rag.protocol import RetrievalCandidate

logger = logging.getLogger("app.rag.reranker")


class RerankResult(BaseModel):
    """离线重排结果；同时保留候选原始名次与重排名次。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    original_rank: int = Field(ge=1)
    rerank_rank: int = Field(ge=1)
    original_score: float
    rerank_score: float


class BaseReranker(ABC):
    """离线 Reranker 抽象，不接入现有生产调用链。"""

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RerankResult]:
        """在候选集合不变的前提下返回完整排序映射。"""


class MockReranker(BaseReranker):
    """恒等重排器：只验证实验链路，不改变候选顺序或分数。"""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RerankResult]:
        del query
        return [
            RerankResult(
                candidate_id=candidate.id,
                original_rank=rank,
                rerank_rank=rank,
                original_score=candidate.confidence_score,
                rerank_score=candidate.confidence_score,
            )
            for rank, candidate in enumerate(candidates, start=1)
        ]


class RealCrossEncoderReranker(BaseReranker):
    """Offline-only CrossEncoder adapter; never registered in production."""

    def __init__(self, model_name: str, *, cache_folder: str, device: str = "cpu",
                 batch_size: int = 8, max_length: int = 512,
                 local_files_only: bool = True) -> None:
        if batch_size < 1 or max_length < 1:
            raise ValueError("batch_size and max_length must be positive")
        from sentence_transformers import CrossEncoder
        self.model_name, self.device = model_name, device
        self.batch_size, self.max_length = batch_size, max_length
        self.cache_folder = str(Path(cache_folder).resolve())
        started = time.perf_counter()
        self._model = CrossEncoder(model_name, cache_folder=self.cache_folder,
                                   device=device, max_length=max_length,
                                   local_files_only=local_files_only)
        self.load_latency_ms = round((time.perf_counter() - started) * 1000, 3)
        self.inference_latencies_ms: list[float] = []

    def rerank(self, query: str,
               candidates: Sequence[RetrievalCandidate]) -> list[RerankResult]:
        if not candidates:
            self.inference_latencies_ms.append(0.0)
            return []
        started = time.perf_counter()
        scores = self._model.predict(
            [(query, candidate.content) for candidate in candidates],
            batch_size=self.batch_size, show_progress_bar=False, convert_to_numpy=True)
        self.inference_latencies_ms.append(round((time.perf_counter() - started) * 1000, 3))
        scored = [(rank, candidate, float(score)) for rank, (candidate, score)
                  in enumerate(zip(candidates, scores), start=1)]
        scored.sort(key=lambda item: (-item[2], item[0]))
        return [RerankResult(candidate_id=candidate.id, original_rank=original_rank,
                             rerank_rank=rerank_rank,
                             original_score=candidate.confidence_score,
                             rerank_score=score)
                for rerank_rank, (original_rank, candidate, score)
                in enumerate(scored, start=1)]


def _sigmoid(x: float) -> float:
    """CrossEncoder logit → (0,1) 分数。"""
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


class Reranker:
    """bge-reranker-base 重排器，失败降级为仅 RRF。"""

    def __init__(self) -> None:
        self.model_name = settings.reranker_model_name
        self._model = None  # type: ignore[var-annotated]
        self._loaded = False
        self.degraded = False
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                from sentence_transformers import CrossEncoder

                from app.rag.embeddings import model_is_cached

                cached = model_is_cached(self.model_name)
                self._model = CrossEncoder(
                    self.model_name,
                    cache_folder=settings.model_cache_dir,
                    local_files_only=cached,  # 已缓存则离线加载，避免运行时联网
                )
                self.degraded = False
                logger.info(
                    "重排模型加载成功：%s（%s）",
                    self.model_name,
                    "离线缓存" if cached else "在线下载",
                )
            except Exception as exc:  # noqa: BLE001 任何失败 → 降级仅 RRF
                self._model = None
                self.degraded = True
                logger.warning(
                    "重排模型 '%s' 加载失败，降级为仅 RRF 排序：%s",
                    self.model_name,
                    exc,
                )
            finally:
                self._loaded = True

    def rerank(
        self, query: str, candidates: list[dict], top_k: int = 5
    ) -> tuple[list[dict], bool]:
        """对候选重排，返回 (排序后候选, rerankerUsed)。

        成功：写入 `score`=sigmoid(rerank logit)，按其降序；
        降级：`score`=RRF 分数归一化（除以最大值），按 rrfScore 降序。
        """
        if not candidates:
            return [], False

        self._ensure_loaded()

        if self._model is not None:
            try:
                pairs = [(query, c["content"]) for c in candidates]
                scores = self._model.predict(pairs)
                for c, s in zip(candidates, scores):
                    c["score"] = round(_sigmoid(float(s)), 4)
                ranked = sorted(candidates, key=lambda x: x["score"], reverse=True)
                return ranked[:top_k], True
            except Exception as exc:  # noqa: BLE001 推理异常 → 本次降级
                logger.warning("重排推理失败，本次降级为仅 RRF 排序：%s", exc)

        # 降级：RRF 归一化分
        max_rrf = max((c.get("rrfScore", 0.0) for c in candidates), default=0.0) or 1.0
        for c in candidates:
            c["score"] = round(c.get("rrfScore", 0.0) / max_rrf, 4)
        ranked = sorted(candidates, key=lambda x: x.get("rrfScore", 0.0), reverse=True)
        return ranked[:top_k], False


_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker


def reset_reranker() -> None:
    """清除单例（供测试模拟「重排模型加载失败」后重新加载）。"""
    global _reranker
    _reranker = None
