"""测验模块 Quiz（接口文档 9.1，B2-b；B6 追加 9.2 错题强化）。

接口 23/24/25：
- GET  /quiz/{kpId}         获取测验题 { questions: QuizQuestion[] }
- POST /quiz/{kpId}/submit  提交作答，判分 ≥70 → passed 并联动掌握度
- POST /reinforce           错题强化生成（薄弱点 + recap + 针对性练习）

均需登录。知识点不存在 → 1004；强化生成失败/审核不通过 → 2001。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import fail, success
from app.core.generation_provenance import traced_generation
from app.core.llm import LLMGenerationError
from app.core.security import get_current_user
from app.models.entities import User
from app.schemas.resource import QuizSubmitRequest, ReinforceRequest
from app.services import quiz as quiz_service

router = APIRouter(tags=["quiz"])


@router.get("/quiz/{kp_id}")
async def get_quiz(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取测验题（接口文档 9.1）。"""
    try:
        data = quiz_service.get_questions(db, kp_id)
    except quiz_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    return success(data)


@router.post("/quiz/{kp_id}/submit")
@traced_generation
async def submit_quiz(
    kp_id: str,
    body: QuizSubmitRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交作答并判分（接口文档 9.1）。≥70 联动掌握度置 passed。"""
    try:
        data = quiz_service.submit(db, user.id, kp_id, body.answers)
    except quiz_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    return success(data)


@router.post("/reinforce")
@traced_generation
async def reinforce(
    body: ReinforceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """错题强化生成（接口文档 9.2）。critic 审核练习题答案自洽后返回。"""
    try:
        data = quiz_service.reinforce(db, user.id, body.kpId, body.wrongQuestionIds)
    except quiz_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)
