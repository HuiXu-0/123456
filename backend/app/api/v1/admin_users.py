"""管理端用户管理接口（B9，接口文档 16.2–16.4）。

三接口全部 require_admin（非管理员 → 1003 / 403）：
- GET  /admin/users                          分页列表（含已通过 KP 数 / 最近活跃）
- POST /admin/users/{userId}/reset-progress  清空进度回到未诊断态（幂等，禁对 admin）
- POST /admin/users/{userId}/toggle-active   翻转 isActive（禁对 admin，防自锁）

响应统一套 1.2 信封；业务校验/状态变更在 app/services/users.py，本层仅编排。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import fail, success
from app.core.security import require_admin
from app.models.entities import User
from app.services import users as users_service

router = APIRouter(tags=["admin-users"])


@router.get("/admin/users")
async def list_users(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理端用户列表（接口文档 16.2）。"""
    return success(users_service.list_users(db, page, pageSize))


@router.post("/admin/users/{user_id}/reset-progress")
async def reset_progress(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """重置用户学习进度（接口文档 16.3）。不存在 → 1004；目标为 admin → 1003。"""
    try:
        return success(users_service.reset_progress(db, user_id))
    except users_service.UserServiceError as err:
        return fail(code=err.code, message=err.message, status_code=err.status_code)


@router.post("/admin/users/{user_id}/toggle-active")
async def toggle_active(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """启用/禁用账号（接口文档 16.4）。不存在 → 1004；目标为 admin → 1003。"""
    try:
        return success(users_service.toggle_active(db, user_id))
    except users_service.UserServiceError as err:
        return fail(code=err.code, message=err.message, status_code=err.status_code)
