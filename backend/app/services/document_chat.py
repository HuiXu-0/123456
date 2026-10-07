"""「和文档对话」——文档问答服务（NotebookLM 式即问即答，接口文档 20.8）。

用户就选中的文档（可多篇）提问 → 在**各文档专属向量集合**上检索（隔离内置库）→ 严格基于
文档内容流式作答、标出处溯源、防幻觉。是「理解文档」的问答，与右栏「基于文档生成资源」相互
独立、并存。

复用点（不重造）：
- 检索/来源：document_generation.retrieve / sources_of（多篇合并检索范围 + 逐条 [n] 溯源）；
- 流式：core.llm.LLMClient.doc_chat_stream（mock 逐字兜底 / deepseek 真实流式）；
- SSE 信封与会话 TTL 取向：与 services.tutor 一致（15.4：data:{"delta"} → event: done；
  异常 event: error）。done 事件额外携带 sources（供前端 SourceTrace 溯源渲染）。

会话上下文：进程内存 dict，TTL 30 分钟（CLAUDE.md 内存 TTL 会话取向，生产替换 Redis）。
与苏格拉底辅导会话池物理分开（本模块独立 _sessions），互不影响。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from app.agents.memory.session_store import SessionStore, register_pool
from app.core.generation_provenance import attach_generation_meta
from app.core.llm import LLMGenerationError, get_llm
from app.services import document_generation as gen

# 会话 TTL：30 分钟（与 tutor 同口径，独立会话池）
SESSION_TTL_SECONDS = 30 * 60

# sessionId → {sessionId, docIds, history:[{role,content}], expiresAt(monotonic)}
# R4.1：4 处分散的 _sessions 统一到 SessionStore（TTL / 惰性 GC / 取建语义不变，
# id 前缀仍为 dc_）
_sessions: SessionStore = register_pool(
    "document_chat",
    SessionStore(ttl_seconds=SESSION_TTL_SECONDS, name="document_chat", id_prefix="dc_"),
)


def _gc() -> None:
    """惰性清理过期会话（访问时触发，无后台线程）。"""
    _sessions.gc()


def _get_session(session_id: str | None, doc_ids: list[str]) -> dict[str, Any]:
    """取/建会话并续期。过期或未知 id → 以该 id（或新 id）重建空上下文。"""
    return _sessions.get_or_create(
        session_id,
        lambda sid: {
            "sessionId": sid,
            "docIds": list(doc_ids),
            "history": [],
        },
    )


def _append_turn(session: dict[str, Any], message: str, reply: str) -> None:
    session["history"].append({"role": "user", "content": message})
    session["history"].append({"role": "assistant", "content": reply})


def _prepare(db: Session, user_id: str, doc_ids: list[str], query: str):
    """解析文档（归属 + 就绪校验）→ 合并检索命中片段 → 组装标题/上下文/来源。"""
    docs = gen.resolve_docs(db, user_id, doc_ids)  # 1004 / 1001 由调用方转信封
    title = gen._merged_title(docs)
    chunks = gen.retrieve(docs, query or title, top_k=gen._RETRIEVE_TOP_K)
    contexts = [c["content"] for c in chunks if c.get("content")]
    sources = gen.sources_of(chunks)
    return docs, title, contexts, sources


def chat(
    db: Session, *, user_id: str, doc_ids: list[str], session_id: str | None, message: str
) -> dict[str, Any]:
    """JSON 整体回复（非流式兜底 / 测试用）：{sessionId, reply, sources}。严格基于文档。"""
    _docs, title, contexts, sources = _prepare(db, user_id, doc_ids, message)
    session = _get_session(session_id, doc_ids)
    llm = get_llm()
    reply = "".join(
        llm.doc_chat_stream(
            source_title=title, contexts=contexts, history=list(session["history"]), message=message
        )
    )
    _append_turn(session, message, reply)
    return {"sessionId": session["sessionId"], "reply": reply, "sources": sources}


def _sse_block(payload: dict[str, Any], event: str | None = None) -> str:
    """SSE 块（15.4）：可选 event 行 + data 行 + 空行分隔。"""
    data = json.dumps(payload, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def sse_stream(
    db: Session, *, user_id: str, doc_ids: list[str], session_id: str | None, message: str
) -> Iterator[str]:
    """SSE 流式回复（接口文档 20.8 / 15.4）。调用方需先确认文档存在/就绪（走 JSON 信封）。

    增量 token → data:{"delta":..}；正常收尾 → event: done {sessionId, sources}（供溯源渲染）；
    生成异常 → event: error {code:2001,...}，已发 delta 由前端保留。
    """
    _docs, title, contexts, sources = _prepare(db, user_id, doc_ids, message)
    session = _get_session(session_id, doc_ids)
    llm = get_llm()
    history = list(session["history"])

    def _events() -> Iterator[str]:
        parts: list[str] = []
        try:
            for delta in llm.doc_chat_stream(
                source_title=title, contexts=contexts, history=history, message=message
            ):
                parts.append(delta)
                yield _sse_block({"delta": delta})
            _append_turn(session, message, "".join(parts))
            yield _sse_block(
                attach_generation_meta(
                    {"sessionId": session["sessionId"], "sources": sources}
                ),
                event="done",
            )
        except LLMGenerationError as exc:
            yield _sse_block({"code": 2001, "message": str(exc)}, event="error")

    return _events()
