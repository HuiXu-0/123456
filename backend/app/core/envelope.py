"""统一响应信封与 traceId 注入。

契约依据：《后端接口文档》§1.2 信封 {code, message, data, traceId}、§1.3 错误码。

设计：
- TraceIdMiddleware 每请求生成短 traceId，存入 contextvar 并回写响应头 X-Trace-Id；
- success()/fail() 助手从 contextvar 取 traceId 产出信封，端点显式 return；
- 异常处理器（在 main.py 注册）把错误也套同一信封形状。

后续阶段（含 WS/SSE 推送）统一复用本模块助手，保证全局信封一致。
"""
from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

# 当前请求的 traceId（请求级隔离）
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
logger = logging.getLogger("app.http")

# HTTP status -> 业务错误码（§1.3）
_STATUS_TO_CODE: dict[int, int] = {
    400: 1001,  # 参数校验失败
    401: 1002,  # 未认证 / token 失效
    403: 1003,  # 无权限
    404: 1004,  # 资源不存在
    500: 5000,  # 服务器内部错误
}


def get_trace_id() -> str:
    """取当前请求的 traceId（无则现生成一个，保证助手在任何上下文可用）。"""
    tid = _trace_id_ctx.get()
    if not tid:
        tid = uuid.uuid4().hex[:12]
        _trace_id_ctx.set(tid)
    return tid


def current_trace_id() -> str:
    """只读取当前 traceId，不在日志等非请求上下文中隐式创建新值。"""
    return _trace_id_ctx.get()


def envelope(data: Any = None, code: int = 0, message: str = "ok") -> dict[str, Any]:
    """构造信封 dict。"""
    return {"code": code, "message": message, "data": data, "traceId": get_trace_id()}


def success(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return envelope(data=data, code=0, message=message)


def fail(code: int, message: str, data: Any = None, status_code: int = 200) -> JSONResponse:
    """构造错误信封响应。"""
    return JSONResponse(
        status_code=status_code,
        content=envelope(data=data, code=code, message=message),
    )


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求注入 traceId，并回写到响应头 X-Trace-Id。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        tid = uuid.uuid4().hex[:12]
        token = _trace_id_ctx.set(tid)
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = _trace_id_ctx.get()
            from app.core.config import settings

            if settings.log_request_completed:
                logger.info(
                    "request_completed method=%s path=%s status=%s durationMs=%.2f",
                    request.method,
                    request.url.path,
                    response.status_code,
                    (time.perf_counter() - started_at) * 1000,
                )
            return response
        except Exception:
            logger.exception(
                "request_failed method=%s path=%s durationMs=%.2f",
                request.method,
                request.url.path,
                (time.perf_counter() - started_at) * 1000,
            )
            raise
        finally:
            _trace_id_ctx.reset(token)


# ---- 异常处理器（在 main.py 注册）-----------------------------------------

async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_TO_CODE.get(exc.status_code, 5000)
    message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
    return fail(code=code, message=message, status_code=exc.status_code)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return fail(code=1001, message="参数校验失败", data=exc.errors(), status_code=400)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return fail(code=5000, message="服务器内部错误", status_code=500)
