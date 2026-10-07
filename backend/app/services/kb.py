"""知识库管理服务（B3，接口文档第 14 章 KB 四件套的业务编排）。

职责：
- ingest：建 KnowledgeDocument 记录（pending）→ 异步任务（抽文本→切片→向量化→入库
  →回填 chunks/status）；
- list/delete：分页列表、删除（同步清理 Chroma 向量 + 元数据）；
- search_test：混合检索 → 重排 → 组装 chunk + 分数 + 来源定位。

文本抽取：pdf 经 pypdf 逐页（source_location 带「第 N 页」）；md/txt 直接解码。
重型向量化在 `asyncio.to_thread` 中执行，避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import io
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.tasks import Task, submit
from app.models.entities import KnowledgeDocument
from app.rag.chunker import DocumentChunker
from app.rag.embeddings import get_embedder
from app.rag.retriever import get_retriever
from app.rag.reranker import get_reranker
from app.rag.vector_store import get_vector_store

logger = logging.getLogger("app.services.kb")

_TEXT_EXTS = {"md", "markdown", "txt", "text"}


# ---- 文档 ID 生成（doc_NNN 顺延，对齐接口文档示例） -----------------------
def _next_doc_id(db: Session) -> str:
    n = db.query(func.count(KnowledgeDocument.id)).scalar() or 0
    # 取已有最大序号 + 1，避免删除后 count 回退导致 ID 冲突
    existing = db.query(KnowledgeDocument.id).all()
    max_seq = 0
    for (did,) in existing:
        if did.startswith("doc_") and did[4:].isdigit():
            max_seq = max(max_seq, int(did[4:]))
    return f"doc_{max(max_seq, n) + 1:03d}"


# ---- 文本抽取 -------------------------------------------------------------
def _extract_segments(filename: str, raw: bytes) -> list[dict]:
    """抽取为切片器输入段：[{text, location}]。pdf 逐页，文本类整篇。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        segs = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                segs.append({"text": text, "location": f"第 {i} 页"})
        return segs
    # md / txt / 其它：按 utf-8 解码（失败用替换字符兜底）
    text = raw.decode("utf-8", errors="replace")
    return [{"text": text, "location": ""}] if text.strip() else []


def _derive_title(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


# ---- 入库（异步任务） -----------------------------------------------------
def _do_ingest(doc_specs: list[tuple[str, str, bytes]], task: Task) -> dict:
    """同步执行：抽文本→切片→向量化→写 Chroma→回填状态。在 to_thread 中调用。"""
    chunker = DocumentChunker(
        chunk_size=settings.chunk_size, overlap=settings.chunk_overlap
    )
    embedder = get_embedder()
    store = get_vector_store()
    db = SessionLocal()
    total = max(1, len(doc_specs))
    indexed = 0
    try:
        for i, (doc_id, filename, raw) in enumerate(doc_specs):
            doc = db.get(KnowledgeDocument, doc_id)
            if doc is None:
                continue
            try:
                doc.status = "indexing"
                db.commit()

                title = doc.title
                all_chunks: list[dict] = []
                for seg in _extract_segments(filename, raw):
                    all_chunks.extend(
                        chunker.chunk_document(
                            seg["text"],
                            document_id=doc_id,
                            document_title=title,
                            base_location=seg["location"],
                        )
                    )
                if all_chunks:
                    ids = [f"{doc_id}#{c['chunk_index']}" for c in all_chunks]
                    contents = [c["content"] for c in all_chunks]
                    metas = [c["metadata"] for c in all_chunks]
                    embeddings = embedder.embed_texts(contents)
                    store.add(ids, embeddings, contents, metas)

                doc.chunks = len(all_chunks)
                doc.status = "indexed"
                db.commit()
                indexed += 1
            except Exception as exc:  # noqa: BLE001 单文档失败不影响其它
                logger.warning("文档 %s 入库失败：%s", doc_id, exc)
                doc.status = "failed"
                db.commit()
            task.progress = int((i + 1) / total * 100)
        return {"indexed": indexed, "total": len(doc_specs)}
    finally:
        db.close()


def ingest_documents(
    db: Session,
    uploads: list[tuple[str, bytes]],
    category: str | None,
    tags: str | None,
) -> dict:
    """登记文档（pending）并提交异步入库任务。返回 {taskId, documents}。"""
    documents: list[dict] = []
    doc_specs: list[tuple[str, str, bytes]] = []
    for filename, raw in uploads:
        doc_id = _next_doc_id(db)
        title = _derive_title(filename)
        doc = KnowledgeDocument(
            id=doc_id,
            title=title,
            filename=filename,
            size=len(raw),
            category=category,
            tags=tags,
            status="pending",
            chunks=0,
        )
        db.add(doc)
        db.flush()  # 让 _next_doc_id 在多文件循环内能看到刚插入的 ID
        documents.append(_doc_to_dict(doc))
        doc_specs.append((doc_id, filename, raw))
    db.commit()

    async def _worker(task: Task):
        return await asyncio.to_thread(_do_ingest, doc_specs, task)

    task = submit(_worker)
    return {"taskId": task.task_id, "documents": documents}


# ---- 列表 / 删除 ----------------------------------------------------------
def list_documents(
    db: Session,
    page: int,
    page_size: int,
    keyword: str | None,
    status: str | None,
) -> dict:
    q = db.query(KnowledgeDocument)
    if keyword:
        q = q.filter(KnowledgeDocument.title.like(f"%{keyword}%"))
    if status:
        q = q.filter(KnowledgeDocument.status == status)
    total = q.count()
    rows = (
        q.order_by(KnowledgeDocument.uploaded_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_doc_to_dict(d) for d in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


def delete_document(db: Session, doc_id: str) -> dict | None:
    """删除文档及其全部向量切片。文档不存在返回 None（路由转 1004）。"""
    doc = db.get(KnowledgeDocument, doc_id)
    if doc is None:
        return None
    removed = get_vector_store().delete_by_document(doc_id)
    db.delete(doc)
    db.commit()
    return {"id": doc_id, "deleted": True, "removedChunks": removed}


# ---- 检索测试 -------------------------------------------------------------
def search_test(query: str, top_k: int = 5) -> dict:
    """混合检索 → 重排 → 组装来源定位（接口文档 14.4）。"""
    # 取较大候选池供 reranker 重排，再裁到 top_k
    pool = max(top_k * 2, 10)
    candidates = get_retriever().search(query, top_k=pool)
    ranked, used = get_reranker().rerank(query, candidates, top_k=top_k)
    results = []
    for c in ranked:
        meta = c.get("metadata", {})
        results.append(
            {
                "chunkId": c["id"],
                "documentId": meta.get("document_id"),
                "documentTitle": meta.get("document_title"),
                "content": c["content"],
                "score": round(float(c.get("score", 0.0)), 4),
                "vectorScore": round(float(c.get("vectorScore", 0.0)), 4),
                "bm25Score": round(float(c.get("bm25Score", 0.0)), 4),
                "sourceLocation": meta.get("source_location", "正文"),
            }
        )
    return {"rerankerUsed": used, "results": results}


# ---- 序列化 ---------------------------------------------------------------
def _doc_to_dict(d: KnowledgeDocument) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "filename": d.filename,
        "size": d.size,
        "category": d.category,
        "status": d.status,
        "chunks": d.chunks,
        "uploadedAt": d.uploaded_at.isoformat() if d.uploaded_at else None,
    }
