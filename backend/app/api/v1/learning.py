"""学习流程模块 Learning Flow（接口文档第 18 章，C2）——费曼 + 康奈尔。

接口 39-44：
- POST /learning/cornell-cues      康奈尔线索生成（cues + noteOutline + summaryHint + sources）
- POST /learning/feynman           费曼讲解评估（JSON；Accept: text/event-stream → SSE）
- GET  /learning/steps/{kpId}      学习步骤进度（6 步 + 透传 7.1 mastery）
- POST /learning/steps/{kpId}      更新学习步骤进度
- GET  /learning/notes/{kpId}      获取康奈尔笔记
- PUT  /learning/notes/{kpId}      保存康奈尔笔记（整体覆盖）

均需登录。知识点不存在 → 1004；难度/步骤非法 → 1001；LLM/Agent 生成失败 → 2001。
不改第 1-17 章任一接口；「已掌握」复用 9.1 → 7.3，本模块不新增掌握度判定（契约 18.6）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import fail, success
from app.core.generation_provenance import traced_generation
from app.core.llm import LLMGenerationError
from app.core.security import get_current_user
from app.models.entities import User
from app.schemas.learning import (
    CornellCuesRequest,
    FeynmanRequest,
    NoteSaveRequest,
    StepUpdateRequest,
)
from app.services import learning_flow as learning_service

router = APIRouter(tags=["learning"])


@router.post("/learning/cornell-cues")
@traced_generation
async def cornell_cues(
    body: CornellCuesRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """康奈尔线索生成（接口文档 18.1）。"""
    try:
        data = learning_service.cornell_cues(
            db, kp_id=body.kpId, difficulty=body.difficulty
        )
    except learning_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except learning_service.InvalidDifficulty:
        return fail(code=1001, message="难度档非法，应为 入门|初级|中级|高级|精通", status_code=400)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)


@router.post("/learning/feynman")
@traced_generation
async def feynman(
    body: FeynmanRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """费曼讲解评估（接口文档 18.2）。

    请求头携带 Accept: text/event-stream → SSE 流式（delta 点评 + event: gaps
    + event: done，异常 event: error）；不带该头 → 整体 JSON（向后兼容）。
    """
    streaming = "text/event-stream" in (request.headers.get("accept") or "")
    try:
        if streaming:
            events = learning_service.feynman_sse_stream(
                db, kp_id=body.kpId, session_id=body.sessionId,
                explanation=body.explanation, user_id=user.id
            )
            return StreamingResponse(
                events,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        data = learning_service.feynman_chat(
            db, kp_id=body.kpId, session_id=body.sessionId,
            explanation=body.explanation, user_id=user.id
        )
    except learning_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)


@router.get("/learning/steps/{kp_id}")
async def get_steps(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取学习步骤进度（接口文档 18.3）。"""
    try:
        data = learning_service.get_steps(db, user_id=user.id, kp_id=kp_id)
    except learning_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    return success(data)


@router.post("/learning/steps/{kp_id}")
async def update_steps(
    kp_id: str,
    body: StepUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新学习步骤进度（接口文档 18.3，幂等）。"""
    try:
        data = learning_service.set_step(
            db, user_id=user.id, kp_id=kp_id, step=body.step, done=body.done
        )
    except learning_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except learning_service.InvalidStep:
        return fail(
            code=1001,
            message="步骤非法，应为 video|lecture|diagram|note|feynman|practice",
            status_code=400,
        )
    return success(data)


@router.get("/learning/notes/{kp_id}")
async def get_note(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取康奈尔笔记（接口文档 18.4）。无数据 → 空笔记占位。"""
    try:
        data = learning_service.get_note(db, user_id=user.id, kp_id=kp_id)
    except learning_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    return success(data)


@router.put("/learning/notes/{kp_id}")
async def save_note(
    kp_id: str,
    body: NoteSaveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保存康奈尔笔记（接口文档 18.5，整体覆盖）。"""
    try:
        data = learning_service.save_note(
            db,
            user_id=user.id,
            kp_id=kp_id,
            cue_notes=[c.model_dump() for c in body.cueNotes],
            main_notes=body.mainNotes,
            summary=body.summary,
        )
    except learning_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    return success(data)
