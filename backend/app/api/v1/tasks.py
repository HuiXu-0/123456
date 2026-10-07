"""任务查询接口（异步任务状态轮询 + 用户任务列表）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.tasks import get_task, list_user_tasks
from app.models.entities import User
from app.schemas.common import fail, success

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}")
async def query_task(
    task_id: str,
    user: User = Depends(get_current_user),
):
    """查询单个任务状态（接口文档 15.2）。"""
    task = get_task(task_id)
    if task is None:
        return fail(code=1004, message="任务不存在", status_code=404)
    # 归属校验：只能查自己的任务（admin 可查所有）
    if task.user_id and task.user_id != user.id and user.role != "admin":
        return fail(code=1003, message="无权限查看该任务", status_code=403)
    return success(task.to_data())


@router.get("/tasks")
async def my_tasks(
    limit: int = 50,
    user: User = Depends(get_current_user),
):
    """当前用户的任务列表（按创建时间倒序）。"""
    items = list_user_tasks(user.id, limit=limit)
    return success({"items": items, "total": len(items)})
