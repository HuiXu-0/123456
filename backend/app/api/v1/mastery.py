"""掌握度与学习旅程模块 Mastery & Journey（接口文档第 7 章，B2-b）。

接口 12/13/14/15：
- GET  /mastery                 掌握度全集 { status: Record<kpId,KPStatus> }
- POST /mastery/{kpId}/check     去检验：learning → pending-check（passed 保持不变）
- POST /mastery/{kpId}/pass      标记通过（幂等）
- GET  /journey                  旅程状态，currentStep 按 7.4 规则推导

均需登录。后端为掌握度权威数据源。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import success
from app.core.security import get_current_user
from app.models.entities import Journey, User
from app.services import mastery as mastery_service

router = APIRouter(tags=["mastery"])


@router.get("/mastery")
async def get_mastery(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """掌握度全集（接口文档 7.1）。未出现的知识点视为未开始。"""
    return success({"status": mastery_service.get_status_map(db, user.id)})


@router.post("/mastery/{kp_id}/check")
async def mastery_check(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """去检验（接口文档 7.2）：learning → pending-check；passed 保持不变。"""
    status = mastery_service.mark_check(db, user.id, kp_id)
    return success({"id": kp_id, "status": status})


@router.post("/mastery/{kp_id}/pass")
async def mastery_pass(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标记通过（接口文档 7.3，幂等）：置 passed。"""
    status = mastery_service.mark_pass(db, user.id, kp_id)
    return success({"id": kp_id, "status": status})


@router.get("/journey")
async def get_journey(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """旅程状态（接口文档 7.4）。currentStep 后端按规则推导。"""
    journey = db.get(Journey, user.id)
    status_map = mastery_service.get_status_map(db, user.id)
    current_step = mastery_service.derive_current_step(db, journey, status_map)
    return success(
        {
            "hasDiagnosed": bool(journey and journey.has_diagnosed),
            "hasGeneratedPath": bool(journey and journey.has_generated_path),
            "targetJobName": journey.target_job_name if journey else None,
            "matchPct": journey.match_pct if journey else None,
            "currentStep": current_step,
        }
    )
