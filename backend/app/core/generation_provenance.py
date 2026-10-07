"""统一记录并注入生成结果的运行时溯源元数据。"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import wraps
from typing import Any

from fastapi.responses import StreamingResponse

from app.core import model_registry
from app.core.config import settings


@dataclass
class GenerationTrace:
    provider: str
    model: str
    source: str
    degraded: bool = False
    fallback_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "source": self.source,
            "degraded": self.degraded,
            "fallbackReason": self.fallback_reason,
        }


_trace_ctx: ContextVar[GenerationTrace | None] = ContextVar(
    "generation_provenance", default=None
)


def new_trace() -> GenerationTrace:
    spec = model_registry.current()
    source = "mock" if spec.provider == "mock" else spec.source
    return GenerationTrace(spec.provider, spec.model_id, source)


@contextmanager
def bind_trace(trace: GenerationTrace | None = None):
    active = trace or new_trace()
    token: Token = _trace_ctx.set(active)
    try:
        yield active
    finally:
        try:
            _trace_ctx.reset(token)
        except ValueError:
            # async generator 跨 context 消费时 token 失效，忽略即可
            pass


def current_trace() -> GenerationTrace | None:
    return _trace_ctx.get()


def mark_execution(*, provider: str, model: str, source: str) -> None:
    trace = current_trace()
    if trace is not None:
        trace.provider = provider
        trace.model = model
        trace.source = source


def mark_provider_fallback(reason: str = "provider_unavailable") -> None:
    trace = current_trace()
    if trace is not None:
        trace.provider = "deepseek"
        trace.model = settings.deepseek_model
        trace.source = "fallback"
        trace.degraded = True
        trace.fallback_reason = reason


def mark_degraded(reason: str = "deterministic_fallback") -> None:
    trace = current_trace()
    if trace is not None:
        trace.provider = "internal"
        trace.model = "deterministic"
        trace.source = "fallback"
        trace.degraded = True
        trace.fallback_reason = reason


def mark_cache() -> None:
    trace = current_trace()
    if trace is not None:
        trace.provider = "cache"
        trace.model = "persisted-artifact"
        trace.source = "cache"


def mark_deterministic() -> None:
    trace = current_trace()
    if trace is not None:
        trace.provider = "internal"
        trace.model = "deterministic"
        trace.source = "deterministic"


def attach_generation_meta(data: Any) -> Any:
    trace = current_trace()
    if trace is None:
        return data
    if isinstance(data, list):
        return [attach_generation_meta(item) for item in data]
    if not isinstance(data, dict):
        return data
    enriched = dict(data)
    enriched["generationMeta"] = trace.as_dict()
    return enriched


def traced_generation(fn: Callable[..., Any]) -> Callable[..., Any]:
    """包裹生成端点；JSON 写入 data，SSE 在迭代期延续同一追踪上下文。"""

    @wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        trace = new_trace()
        with bind_trace(trace):
            result = await fn(*args, **kwargs)
            if isinstance(result, dict) and result.get("code") == 0:
                result = dict(result)
                result["data"] = attach_generation_meta(result.get("data"))
                return result
            if isinstance(result, StreamingResponse):
                original = result.body_iterator

                async def traced_body() -> AsyncIterator[Any]:
                    with bind_trace(trace):
                        async for chunk in original:
                            yield chunk

                result.body_iterator = traced_body()
            return result

    return wrapped
