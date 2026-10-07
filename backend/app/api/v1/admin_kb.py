"""管理端知识库接口（B3，接口文档第 14 章 14.1–14.4）。

四件套，全部 require_admin（非管理员 → 1003 / 403）：
- POST   /admin/kb/upload            multipart 上传（pdf/md/txt），返回 taskId + documents（pending）
- GET    /admin/kb/documents         分页列表（keyword/status 过滤）
- DELETE /admin/kb/documents/{id}    删除文档 + 向量切片（不存在 → 1004）
- POST   /admin/kb/search-test       BM25+向量 RRF → reranker 重排，返回 chunk + 分数 + 来源

响应统一套 1.2 信封；切片/向量化/检索算法在 app/rag/* 实现，本层仅做编排与校验。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import fail, success
from app.core.security import require_admin
from app.models.entities import User
from app.services import kb as kb_service

router = APIRouter(tags=["admin-kb"])


class SearchTestRequest(BaseModel):
    query: str = Field(..., min_length=1)
    topK: int = Field(default=5, ge=1, le=50)


@router.post("/admin/kb/upload")
async def upload_documents(
    files: list[UploadFile] = File(...),
    category: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """知识库文档上传（接口文档 14.1）。切片→向量化→入库为异步任务，返回 taskId。"""
    uploads: list[tuple[str, bytes]] = []
    for f in files:
        if not f.filename:
            continue
        uploads.append((f.filename, await f.read()))
    if not uploads:
        return fail(code=1001, message="未提供有效文件", status_code=400)
    data = kb_service.ingest_documents(db, uploads, category, tags)
    return success(data)


@router.get("/admin/kb/documents")
async def list_documents(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """知识库文档列表（接口文档 14.2）。"""
    data = kb_service.list_documents(db, page, pageSize, keyword, status)
    return success(data)


@router.delete("/admin/kb/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """删除知识库文档及其向量切片（接口文档 14.3）。不存在 → 1004。"""
    data = kb_service.delete_document(db, doc_id)
    if data is None:
        return fail(code=1004, message="文档不存在", status_code=404)
    return success(data)


@router.post("/admin/kb/search-test")
async def search_test(
    body: SearchTestRequest,
    admin: User = Depends(require_admin),
):
    """检索测试（接口文档 14.4）。混合检索 + 重排，reranker 不可用时降级仅 RRF。"""
    data = kb_service.search_test(body.query, body.topK)
    return success(data)
