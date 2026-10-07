"""画像诊断模块 Profile（接口文档第 4 章，B2-a）。

接口 4/5/6/7：
- POST /profile/parse           多模态材料解析（multipart）
- POST /profile/narrative       画像叙述生成（无材料返回 null）
- POST /profile/diagnosis-complete  完成诊断，写旅程
- GET  /profile/ability-portrait    能力雷达 6 维

均需登录（get_current_user）。生成调用经 LLMClient（在 service 层）；
真实模式 LLM 失败 → 2001 / HTTP 500（B5-b，接口文档 1.3）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import fail, success
from app.core.generation_provenance import traced_generation
from app.core.llm import LLMGenerationError
from app.core.security import get_current_user
from app.models.entities import User
from app.schemas.profile import (
    DialogueRequest,
    DiagnosisCompleteRequest,
    NarrativeRequest,
    StudentPortraitWriteRequest,
)
from app.services import profile as profile_service
from app.services import profile_dialogue as dialogue_service
from app.services import student_portrait as portrait_service

router = APIRouter(tags=["profile"])


@router.post("/profile/parse")
@traced_generation
async def parse(
    files: list[UploadFile] | None = File(default=None),
    description: str | None = Form(default=None),
    user: User = Depends(get_current_user),
):
    """多模态材料解析（接口文档 4.1）。files/description 均可空。"""
    uploads: list[tuple[str, bytes]] = []
    for f in files or []:
        if not f.filename:
            continue
        uploads.append((f.filename, await f.read()))
    try:
        data = profile_service.parse_profile(user, uploads, description)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)


@router.post("/profile/narrative")
@traced_generation
async def narrative(
    body: NarrativeRequest,
    user: User = Depends(get_current_user),
):
    """画像叙述生成（接口文档 4.2）。无材料 → data 为 null。"""
    try:
        data = profile_service.generate_narrative(body)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)


@router.post("/profile/diagnosis-complete")
async def diagnosis_complete(
    body: DiagnosisCompleteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """完成诊断，写入旅程状态（接口文档 4.3）。"""
    data = profile_service.complete_diagnosis(db, user, body)
    return success(data)


@router.get("/profile/ability-portrait")
async def ability_portrait(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """能力雷达数据（接口文档 4.4，C2 由微测/quiz 写入的 Mastery 能力分驱动）。"""
    data = profile_service.ability_portrait(db, user)
    return success(data)


@router.post("/profile/dialogue")
@traced_generation
async def dialogue(
    body: DialogueRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对话式画像诊断（接口文档 17.1，C1-b）。

    请求头携带 Accept: text/event-stream → SSE 流式（delta 逐句 + event: portrait
    维度增量 + event: done，异常 event: error）；不带该头 → 整体 JSON（向后兼容）。
    与既有 4.4 ability-portrait 并存，构建独立的异质学生画像（17.2）。
    """
    streaming = "text/event-stream" in (request.headers.get("accept") or "")
    if streaming:
        events = dialogue_service.sse_stream(
            user_id=user.id,
            session_id=body.sessionId,
            message=body.message,
            answer=body.answer,
            context=body.context,
        )
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    try:
        data = dialogue_service.dialogue(
            db,
            user_id=user.id,
            session_id=body.sessionId,
            message=body.message,
            answer=body.answer,
            context=body.context,
        )
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)


@router.get("/profile/student-portrait")
async def student_portrait(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取异质学生动态画像（接口文档 17.3）。无数据 → 空画像占位（dimensions:[]）。"""
    data = portrait_service.get_portrait(db, user.id)
    return success(data)


@router.put("/profile/student-portrait")
async def write_student_portrait(
    body: StudentPortraitWriteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """覆盖写入权威画像（接口文档 17.4，C-fix）。

    简历 / 手动填写路径「确认画像」时调用：把表单输入映射为与对话诊断同一套
    canonical 维度后整份覆盖写入 StudentPortrait，使三条路径产出同一份、同维度的
    权威画像；重做即覆盖、不分叉（与对话诊断 17.1 边问边抽共用同一行）。
    """
    dimensions = [d.model_dump(exclude_none=True) for d in body.dimensions]
    data = portrait_service.replace_portrait(db, user.id, dimensions)
    return success(data)
