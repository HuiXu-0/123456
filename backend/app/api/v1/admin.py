"""管理端鉴权哨兵（B1）。

B1 只负责把 `require_admin` 依赖接入一条受保护路由，用于验收「learner → 1003 /
无 token → 1002」。真正的管理端业务接口（KB / Prompt / 指标，接口文档第 14 章）在
B3/B4 落地，届时所有 /admin/* 路由复用同一 require_admin 依赖。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.envelope import success
from app.core.security import require_admin
from app.models.entities import User

router = APIRouter(tags=["admin"])


@router.get("/admin/ping", dependencies=[Depends(require_admin)])
async def admin_ping(user: User = Depends(require_admin)):
    """管理员存活探针——仅用于校验 require_admin 守卫。"""
    return success({"pong": True, "userId": user.id, "role": user.role})
