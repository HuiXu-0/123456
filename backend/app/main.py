"""FastAPI 应用入口。

B0：CORS + traceId 中间件 + 统一信封异常处理 + 挂载 /api/v1 路由。
B1：启动时初始化数据库 + 种子 + 日志脱敏；挂载 auth / admin 路由。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import (
    admin,
    admin_kb,
    admin_metrics,
    admin_prompts,
    admin_users,
    auth,
    dashboard,
    document,
    health,
    job_market,
    knowledge_graph,
    knowledge_system,
    learning,
    learning_path,
    mastery,
    models,
    profile,
    quiz,
    resource,
    resource_history,
    tasks,
    tts,
    workflow,
)
from app.core.config import settings
from app.core.envelope import (
    TraceIdMiddleware,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.init_db import init_db
from app.core.logging import setup_logging
from app.core.openapi_contract import install_openapi_contract
from app.core.security import UserContextMiddleware
from app.core.tasks import recover_tasks
from app.rag.embeddings import get_embedder

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：装脱敏过滤器 + 建表灌种子（幂等）
    setup_logging()
    init_db()
    task_recovery = recover_tasks()
    logger.info(
        "task_recovery restored=%s interrupted=%s",
        task_recovery["restored"],
        task_recovery["interrupted"],
    )
    embedding_status = get_embedder().status(load=True)
    logger.info(
        "embedding_runtime mode=%s provider=%s model=%s dimension=%s fallbackAllowed=%s loadFailed=%s",
        embedding_status["mode"],
        embedding_status["provider"],
        embedding_status["modelName"],
        embedding_status["dimension"],
        embedding_status["fallbackAllowed"],
        bool(embedding_status["loadError"]),
    )
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# CORS（暴露 X-Trace-Id 便于前端排查）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)

# traceId 注入（CORS 之后添加 → 请求时先于 CORS 执行，保证响应头写入）
app.add_middleware(TraceIdMiddleware)

# 用户上下文注入（模型管理 21.3+：per-user 当前模型解析；best-effort，不改鉴权语义）
app.add_middleware(UserContextMiddleware)

# 统一信封异常处理
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# 路由挂载
app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(admin.router, prefix=settings.api_prefix)
# B2-a：Profile / LearningPath / Task
app.include_router(profile.router, prefix=settings.api_prefix)
app.include_router(learning_path.router, prefix=settings.api_prefix)
app.include_router(tasks.router, prefix=settings.api_prefix)
# B2-b：Mastery & Journey / Resource / Quiz
app.include_router(mastery.router, prefix=settings.api_prefix)
app.include_router(models.router, prefix=settings.api_prefix)
app.include_router(resource.router, prefix=settings.api_prefix)
app.include_router(resource_history.router, prefix=settings.api_prefix)
app.include_router(quiz.router, prefix=settings.api_prefix)
# B3：管理端知识库 RAG 四件套
app.include_router(admin_kb.router, prefix=settings.api_prefix)
# B4-b：管理端 Prompt 热更新 + 指标看板
app.include_router(admin_prompts.router, prefix=settings.api_prefix)
app.include_router(admin_metrics.router, prefix=settings.api_prefix)
# B9：账号体系——注册在 auth；管理端用户管理三接口
app.include_router(admin_users.router, prefix=settings.api_prefix)
# B6：P1 特色——岗位市场 / 知识图谱 / 学情概览（Reinforce 在 quiz、External 在 resource）
app.include_router(job_market.router, prefix=settings.api_prefix)
app.include_router(knowledge_graph.router, prefix=settings.api_prefix)
# 会话一：78 点 / 7 板块知识体系目录 + 板块级覆盖（新增端点，向后兼容）
app.include_router(knowledge_system.router, prefix=settings.api_prefix)
app.include_router(dashboard.router, prefix=settings.api_prefix)
# B7-a：多智能体工作流实时通道（execute / 轮询 / WS 推送；Tutor SSE 与 Video 在 resource）
app.include_router(workflow.router, prefix=settings.api_prefix)
# C2：学习流程模块（费曼 + 康奈尔，接口文档第 18 章；新增 /learning/* 接口 39-44）
app.include_router(learning.router, prefix=settings.api_prefix)
# TTS：语音合成（edge-tts 神经语音，接口文档 8.9；新增 /tts/* 接口 45）
app.include_router(tts.router, prefix=settings.api_prefix)
# 文档学习：上传文档 → 专属向量集合 → 基于文档生成讲义/视频/图解/导图/练习题/闪卡（接口文档第 20 章）
app.include_router(document.router, prefix=settings.api_prefix)

# 讲解视频 mp4 静态产物（CC-video-mp4 第二步）：挂在 api_prefix 下复用前端 /api 代理，
# videoUrl=/api/v1/media/video/<file>.mp4 可直接 <video> 播放 / 下载。目录入 .gitignore。
from app.services.video_render import static_mount  # noqa: E402

_mnt = static_mount()
if _mnt is not None:
    _url_path, _directory = _mnt
    app.mount(_url_path, StaticFiles(directory=_directory), name="video-media")

# TASK-006-B：让 /openapi.json 显式描述统一信封和实际 400 校验错误，并支持快照防漂移。
install_openapi_contract(app)
