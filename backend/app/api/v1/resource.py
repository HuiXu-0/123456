"""学习资源模块 Resource（接口文档第 8 章，B2-b；B6 追加 8.6；B7-a 追加 8.3/8.7）。

接口 16/17/18/19/20/21/22：
- GET  /resource/knowledge-point/{kpId}  知识点元信息 { id,name,description,status }
- POST /resource/lecture                  自适应讲义（markdown+sources+hallucinationRate）
- POST /resource/video                    视频讲解（videoUrl:null + narration[] 帧-旁白映射）
- GET  /resource/mindmap/{kpId}           思维导图 { markdown }（Markmap 语法，B8 补齐）
- GET  /resource/diagram/{kpId}           Mermaid 图解 { mermaid }（B8 补齐）
- GET  /resource/external/{kpId}          外部精选资源（relevance/credibility/reason）
- POST /resource/tutor/chat               苏格拉底辅导（JSON；Accept: text/event-stream
                                          时按 15.4 切 SSE 流式：delta 逐条 + done 收尾）

均需登录。生成走 service → LLMClient（mock 确定性 / deepseek 真实工作流）。
知识点不存在 → 1004；难度档非法 → 1001；LLM/Agent 生成失败 → 2001。
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
from app.schemas.resource import (
    ExternalAggregateRequest,
    LectureRequest,
    TutorChatRequest,
    TutorGenerateRequest,
    TutorSuggestRequest,
    VideoRenderRequest,
    VideoRequest,
)
from app.services import generation_log as generation_log_service
from app.services import resource as resource_service
from app.services import resource_search as resource_search_service
from app.services import tutor as tutor_service
from app.services import tutor_resource as tutor_resource_service
from app.services import video_render as video_render_service

router = APIRouter(tags=["resource"])


@router.get("/resource/knowledge-point/{kp_id}")
async def knowledge_point_meta(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """知识点元信息（接口文档 8.1）。"""
    try:
        data = resource_service.knowledge_point_meta(db, user.id, kp_id)
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    return success(data)


@router.get("/resource/mindmap/{kp_id}")
@traced_generation
async def mindmap(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """思维导图（接口文档 8.4）。Markmap 语法 Markdown 大纲。"""
    try:
        data = resource_service.mindmap(db, kp_id)
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    return success(data)


@router.get("/resource/diagram/{kp_id}")
@traced_generation
async def diagram(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mermaid 知识图解（接口文档 8.5）。"""
    try:
        data = resource_service.diagram(db, kp_id)
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    # 「我的资源库」埋点（旁路追加，不改生成逻辑）：图解无难度档 → difficulty=""
    generation_log_service.record(db, user.id, kp_id, "diagram", "")
    return success(data)


@router.get("/resource/external/{kp_id}")
async def external_resources(
    kp_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """外部资源聚合（接口文档 8.6）。精选资源按相关度降序。"""
    try:
        data = resource_service.external_resources(db, kp_id)
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    return success(data)


@router.post("/resource/external/aggregate")
@traced_generation
async def external_aggregate(
    body: ExternalAggregateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """外部资源·AI 联网搜索聚合（接口文档 8.6 增量，C-fix 批3-bonus）。

    按当前知识点 + 用户薄弱点联网搜索 → 聚合 Agent 整理 + critic 评分；无搜索能力 →
    种子兜底（online=false）。后端代理联网，规避配额/跨域。mock 兜底可跑。
    """
    try:
        data = resource_search_service.aggregate(
            db, user.id, body.kpId, body.query, body.weakPoints
        )
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)


@router.post("/resource/lecture")
@traced_generation
async def generate_lecture(
    body: LectureRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成自适应讲义（接口文档 8.2）。"""
    try:
        data = resource_service.generate_lecture(db, user.id, body.kpId, body.difficulty)
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except resource_service.InvalidDifficulty:
        return fail(code=1001, message="难度档非法，应为 入门|初级|中级|高级|精通", status_code=400)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    # 「我的资源库」埋点（旁路追加，不改生成逻辑）
    generation_log_service.record(db, user.id, body.kpId, "lecture", body.difficulty)
    return success(data)


@router.post("/resource/video")
@traced_generation
async def generate_video(
    body: VideoRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成视频讲解（接口文档 8.3，B7-a）。videoUrl:null → 前端 Remotion Player。"""
    try:
        data = resource_service.generate_video(db, user.id, body.kpId, body.difficulty)
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except resource_service.InvalidDifficulty:
        return fail(code=1001, message="难度档非法，应为 入门|初级|中级|高级|精通", status_code=400)
    # 「我的资源库」埋点（旁路追加，不改生成逻辑）
    generation_log_service.record(db, user.id, body.kpId, "video", body.difficulty)
    return success(data)


@router.post("/resource/video/render")
async def render_video(
    body: VideoRenderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """触发讲解视频服务端渲染为 mp4（接口文档 8.3 增量，CC-video-mp4 第二步）。

    返回 { status, taskId, videoUrl }：
    - ready：已有渲染产物，videoUrl 可直接播放/下载（命中缓存「秒出」）；
    - rendering：后台渲染中，前端轮询 GET /tasks/{taskId}，succeeded 后取 result.videoUrl；
    - unavailable：无渲染能力 / 关闭 → videoUrl=null，前端回落实时 Remotion Player + TTS。

    渲染是增强项：本接口绝不阻断「拿不到 mp4 也能看」的兜底；耗时渲染不入请求、走任务轮询。
    """
    try:
        data = video_render_service.start_render(db, user.id, body.kpId, body.difficulty)
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except resource_service.InvalidDifficulty:
        return fail(code=1001, message="难度档非法，应为 入门|初级|中级|高级|精通", status_code=400)
    return success(data)


@router.post("/resource/tutor/chat")
@traced_generation
async def tutor_chat(
    body: TutorChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """苏格拉底辅导（接口文档 8.7 + 15.4，B7-a）。

    请求头携带 Accept: text/event-stream → SSE 流式（15.4：data:{"delta":..} 逐条
    + event: done 收尾，异常 event: error）；不带该头 → 8.7 整体 JSON（向后兼容）。
    """
    streaming = "text/event-stream" in (request.headers.get("accept") or "")
    try:
        if streaming:
            events = tutor_service.sse_stream(
                db, kp_id=body.kpId, session_id=body.sessionId, message=body.message
            )
            return StreamingResponse(
                events,
                media_type="text/event-stream",
                # 关闭代理缓冲，保证逐 delta 实时下发（Vite/Nginx 透传）
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        data = tutor_service.chat(
            db, kp_id=body.kpId, session_id=body.sessionId, message=body.message
        )
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)


@router.post("/resource/tutor/suggest")
@traced_generation
async def tutor_suggest(
    body: TutorSuggestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """智能辅导·问题点识别 + 资源生成清单（接口文档 8.8，C-fix 批3-bonus）。"""
    try:
        data = tutor_resource_service.suggest(db, body.kpId, body.question)
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)


@router.post("/resource/tutor/generate")
@traced_generation
async def tutor_generate(
    body: TutorGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """智能辅导·按需生成勾选的针对性资源（接口文档 8.8）。复用既有讲义/图解/视频/例题能力。"""
    try:
        data = tutor_resource_service.generate(
            db, user.id, body.kpId, body.problemPoint, body.types
        )
    except resource_service.UnknownKnowledgePoint:
        return fail(code=1004, message="知识点不存在", status_code=404)
    except resource_service.InvalidDifficulty:
        return fail(code=1001, message="难度档非法", status_code=400)
    except LLMGenerationError as exc:
        return fail(code=2001, message=f"LLM/Agent 生成失败：{exc}", status_code=500)
    return success(data)
