"""管理端 Prompt 模板接口（B4-b，接口文档 14.5）。

- GET /admin/prompts/{agentId}  完整 PromptTemplate
- PUT /admin/prompts/{agentId}  保存即热更新（version 自增，B5 经 services.prompts.get_template 现读生效）

均 require_admin（非管理员 → 1003/403）；未知 agentId → 1004/404；
缺失必需占位符 → 1001/400。响应统一套 1.2 信封。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import fail, success
from app.core.security import require_admin
from app.models.entities import User
from app.services import prompts as prompts_service

router = APIRouter(tags=["admin-prompts"])


class PromptUpdateRequest(BaseModel):
    template: str = Field(..., min_length=1)


@router.get("/admin/prompts/{agent_id}")
async def get_prompt_template(
    agent_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """获取 Agent Prompt 模板（接口文档 14.5）。"""
    data = prompts_service.get_prompt(db, agent_id)
    if data is None:
        return fail(code=1004, message="Agent 不存在", status_code=404)
    return success(data)


@router.put("/admin/prompts/{agent_id}")
async def update_prompt_template(
    agent_id: str,
    body: PromptUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """更新 Agent Prompt 模板，保存即热更新（接口文档 14.5）。"""
    try:
        data = prompts_service.update_prompt(db, agent_id, body.template)
    except prompts_service.MissingPlaceholderError as exc:
        return fail(code=1001, message=str(exc), status_code=400)
    if data is None:
        return fail(code=1004, message="Agent 不存在", status_code=404)
    return success(data)
