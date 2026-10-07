"""用户文档服务（「文档学习」平行链路）：上传登记 → 异步入库（专属向量集合）→ 列表/删除。

与内置知识库 services.kb（管理端、全局 kb_chunks 集合）**完全平行、互不影响**：
- 每篇文档一个专属 collection（`doclearn_<docId>`）——Chroma 命名空间隔离，
  与内置课程知识库 kb_chunks 物理分开、互不污染（CC 铁律）；
- 复用现有 RAG 组件：DocumentChunker 切片、Embedder 向量化、命名集合向量库；
- ingest 走异步任务（taskId 轮询），状态机 pending→indexing→indexed/failed。

无外键耦合（user_id 隔离键）；user 只能操作自己的文档（路由层校验归属）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.tasks import Task, submit
from app.models.entities import Document
from app.rag.chunker import DocumentChunker
from app.rag.embeddings import get_embedder
from app.rag.vector_store import drop_collection, get_collection_store
from app.services import document_parse
from app.services import generation_log as generation_log_service

logger = logging.getLogger("app.services.document_store")


class UnknownDocument(Exception):
    """文档不存在或不属于当前用户（→ code 1004 / 404）。"""


def _new_doc_id() -> str:
    return "udoc_" + uuid.uuid4().hex[:12]


def _collection_of(doc_id: str) -> str:
    return f"doclearn_{doc_id}"


def _doc_to_dict(d: Document) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "filename": d.filename,
        "fileType": d.file_type,
        "size": d.size,
        "status": d.status,
        "chunks": d.chunks,
        "error": d.error,
        "uploadedAt": d.uploaded_at.isoformat() if d.uploaded_at else None,
    }


# ---- 归属校验 --------------------------------------------------------------
def require_document(db: Session, user_id: str, doc_id: str) -> Document:
    """取当前用户的文档；不存在或非本人 → UnknownDocument（路由转 1004）。"""
    doc = db.get(Document, doc_id)
    if doc is None or doc.user_id != user_id:
        raise UnknownDocument(doc_id)
    return doc


# ---- 入库（异步任务，写文档专属集合） --------------------------------------
def _do_ingest(doc_id: str, filename: str, raw: bytes, task: Task) -> dict:
    """同步执行：抽文本→切片→向量化→写文档专属 Chroma 集合→回填状态。在 to_thread 中调用。"""
    chunker = DocumentChunker(
        chunk_size=settings.chunk_size, overlap=settings.chunk_overlap
    )
    embedder = get_embedder()
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            return {"indexed": 0, "chunks": 0}
        try:
            doc.status = "indexing"
            db.commit()

            all_chunks: list[dict] = []
            for seg in document_parse.extract_segments(filename, raw):
                all_chunks.extend(
                    chunker.chunk_document(
                        seg["text"],
                        document_id=doc_id,
                        document_title=doc.title,
                        base_location=seg["location"],
                    )
                )
            # 重编全局 chunk_index：chunk_document 按 segment 独立编号，跨 segment 会重复
            for i, c in enumerate(all_chunks):
                c["chunk_index"] = i
                c["metadata"]["chunk_index"] = i
            if all_chunks:
                store = get_collection_store(doc.collection)
                ids = [f"{doc_id}#{c['chunk_index']}" for c in all_chunks]
                contents = [c["content"] for c in all_chunks]
                metas = [c["metadata"] for c in all_chunks]
                embeddings = embedder.embed_texts(contents)
                store.add(ids, embeddings, contents, metas)

            doc.chunks = len(all_chunks)
            doc.status = "indexed" if all_chunks else "failed"
            doc.error = None if all_chunks else "未能从文档中抽取到可用文本"
            task.progress = 100
            db.commit()
            return {"indexed": 1 if all_chunks else 0, "chunks": len(all_chunks)}
        except Exception as exc:  # noqa: BLE001 单文档失败落 failed，不外抛崩任务
            logger.warning("文档 %s 入库失败：%s", doc_id, exc)
            doc.status = "failed"
            doc.error = str(exc)[:250]
            db.commit()
            return {"indexed": 0, "chunks": 0, "error": str(exc)}
    finally:
        db.close()


def upload_document(
    db: Session, user_id: str, filename: str, raw: bytes
) -> dict:
    """登记文档（pending）+ 落解析文本，提交异步入库任务。返回 {taskId, document}。

    解析在请求内同步完成（快、便于早失败）；向量化/入库走异步任务（耗时操作纪律）。
    不支持的格式在此抛 UnsupportedDocument（路由转 1001）。
    """
    file_type = document_parse.file_type_of(filename)  # 不支持 → UnsupportedDocument
    title = document_parse.derive_title(filename)
    content = document_parse.extract_text(filename, raw)
    doc_id = _new_doc_id()
    doc = Document(
        id=doc_id,
        user_id=user_id,
        title=title,
        filename=filename,
        file_type=file_type,
        size=len(raw),
        collection=_collection_of(doc_id),
        content=content,
        status="pending",
        chunks=0,
    )
    db.add(doc)
    db.commit()
    document = _doc_to_dict(doc)

    async def _worker(task: Task):
        return await asyncio.to_thread(_do_ingest, doc_id, filename, raw, task)

    task = submit(_worker)
    return {"taskId": task.task_id, "document": document}


# ---- 列表 / 详情 / 删除 ----------------------------------------------------
def list_documents(db: Session, user_id: str) -> dict:
    """列出当前用户的全部文档（按上传时间倒序）。"""
    rows = (
        db.query(Document)
        .filter(Document.user_id == user_id)
        .order_by(Document.uploaded_at.desc(), Document.id.desc())
        .all()
    )
    return {"items": [_doc_to_dict(d) for d in rows], "total": len(rows)}


def get_document(db: Session, user_id: str, doc_id: str) -> dict:
    """文档详情（含解析文本预览）。非本人/不存在 → UnknownDocument。"""
    doc = require_document(db, user_id, doc_id)
    data = _doc_to_dict(doc)
    data["contentPreview"] = (doc.content or "")[:2000]
    return data


def delete_document(db: Session, user_id: str, doc_id: str) -> dict:
    """删除文档及其专属向量集合。非本人/不存在 → UnknownDocument。

    连带清理该文档在「我的资源库」的资产行（含已落库产物）——避免删文档后残留孤儿资产
    （旁路 best-effort，不影响文档删除主链路）。
    """
    doc = require_document(db, user_id, doc_id)
    drop_collection(doc.collection)
    db.delete(doc)
    db.commit()
    removed = generation_log_service.delete_document_rows(db, user_id, doc_id)
    return {"id": doc_id, "deleted": True, "removedResources": removed}
