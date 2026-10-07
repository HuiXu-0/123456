"""我的资源库·生成历史（接口文档第 19 章）。

- GET    /resource/history          按当前用户分页列出其资源生成历史（可按 kind/知识点/时间过滤）
- POST   /resource/history/log      追加一条生成埋点（供前端为「无独立后端生成接口」的形态补埋点：
                                    思维导图 mindmap / 代码实操 code；讲义/视频/图解由各自生成接口
                                    在后端旁路自动埋点，此接口 upsert 幂等、重复调用安全）
- POST   /resource/history/rename   重命名一条资产的展示标题（CRUD·改；按 user 校验归属）
- DELETE /resource/history/{logId}  删除一条资产（CRUD·删；连带已落库产物；按 user 校验归属）

均需登录；user 归属取自 JWT（get_current_user），与 ResourceCache 全局缓存无关。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import fail, success
from app.core.security import get_current_user
from app.models.entities import User
from app.services import generation_log as generation_log_service

router = APIRouter(tags=["resource-history"])


class HistoryLogRequest(BaseModel):
    """生成埋点追加请求（接口文档 19.2）。kind 为五类基类之一；非白名单静默忽略。"""

    kpId: str
    kind: str  # lecture|video|diagram|mindmap|code
    difficulty: str = ""
    title: str | None = None
    resourceRef: str | None = Field(default=None)


class HistoryRenameRequest(BaseModel):
    """重命名资产标题请求（接口文档 19.3）。仅改展示标题，不改类型/归属。"""

    id: int
    title: str = Field(..., min_length=1, max_length=256)


@router.get("/resource/history")
async def resource_history(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    kind: str | None = Query(default=None),
    kpId: str | None = Query(default=None),
    startTime: str | None = Query(default=None),
    endTime: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当前用户的资源生成历史（接口文档 19.1）。按生成时间倒序，支持 kind/知识点/时间过滤。"""
    data = generation_log_service.list_history(
        db,
        user.id,
        page=page,
        page_size=pageSize,
        kind=kind,
        kp_id=kpId,
        start_time=startTime,
        end_time=endTime,
    )
    return success(data)


@router.post("/resource/history/log")
async def resource_history_log(
    body: HistoryLogRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """追加一条生成埋点（接口文档 19.2）。upsert 幂等；非五类 kind 静默忽略。"""
    generation_log_service.record(
        db,
        user.id,
        body.kpId,
        body.kind,
        body.difficulty,
        title=body.title,
        resource_ref=body.resourceRef,
    )
    return success({"logged": True})


@router.post("/resource/history/rename")
async def resource_history_rename(
    body: HistoryRenameRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重命名资源库资产标题（接口文档 19.3，CRUD·改）。非本人/不存在 → 1004。"""
    item = generation_log_service.rename(db, user.id, body.id, body.title)
    if item is None:
        return fail(code=1004, message="资源不存在或无权操作", status_code=404)
    return success(item)


@router.delete("/resource/history/{log_id}")
async def resource_history_delete(
    log_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除资源库资产（接口文档 19.4，CRUD·删；连带其已落库产物）。非本人/不存在 → 1004。"""
    if not generation_log_service.delete(db, user.id, log_id):
        return fail(code=1004, message="资源不存在或无权操作", status_code=404)
    return success({"id": log_id, "deleted": True})
