"""健康检查接口。"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.core.envelope import success

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """GET /api/v1/health —— 返回统一信封。"""
    return success({"status": "ok", "service": settings.app_name})
