"""苏格拉底辅导服务（接口文档 8.7 + 15.4，B7-a）。

- 会话上下文：进程内存 dict，TTL 30 分钟（CLAUDE.md 内存 TTL 会话取向；
  生产替换 Redis，路径写 README）。过期/未知 sessionId → 以该 id 重建空上下文
  （前端无感知，历史自然清零）；
- JSON 模式（8.7）：{sessionId, reply, suggestions}；
- SSE 模式（15.4）：data:{"delta":..} 逐条 → event: done 携带 sessionId/suggestions；
  生成异常 → event: error {code:2001,...}（前端保留已渲染片段）。
- mock：确定性引导链逐字流式；deepseek：真实流式 + system 约束
  「引导式提问不直接给答案」（prompt 见 core.llm._TUTOR_SYSTEM）。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from app.agents.memory.session_store import SessionStore, register_pool
from app.core.generation_provenance import attach_generation_meta
from app.core.llm import LLMGenerationError, get_llm
from app.models.entities import KnowledgePoint
from app.services.resource import UnknownKnowledgePoint

# 会话 TTL：30 分钟（执行方案 B7「会话上下文内存 TTL」）
SESSION_TTL_SECONDS = 30 * 60

# sessionId → {sessionId, kpId, history: [{role, content}], expiresAt(monotonic)}
# R4.1：4 处分散的 _sessions 统一到 SessionStore（TTL / 惰性 GC / 取建语义不变，
# id 前缀仍为 s_；既有测试 _sessions[sid]["expiresAt"]=0.0 的戳法因 dict 兼容照旧可用）
_sessions: SessionStore = register_pool(
    "tutor",
    SessionStore(ttl_seconds=SESSION_TTL_SECONDS, name="tutor", id_prefix="s_"),
)


def _gc() -> None:
    """惰性清理过期会话（访问时触发，无后台线程）。"""
    _sessions.gc()


def _get_session(session_id: str | None, kp_id: str) -> dict[str, Any]:
    """取/建会话并续期。过期或未知 id → 以该 id（或新 id）重建空上下文。"""
    return _sessions.get_or_create(
        session_id,
        lambda sid: {"sessionId": sid, "kpId": kp_id, "history": []},
    )


def _require_kp(db: Session, kp_id: str) -> KnowledgePoint:
    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise UnknownKnowledgePoint(kp_id)
    return kp


def _append_turn(session: dict[str, Any], message: str, reply: str) -> None:
    session["history"].append({"role": "user", "content": message})
    session["history"].append({"role": "assistant", "content": reply})


def chat(
    db: Session, *, kp_id: str, session_id: str | None, message: str
) -> dict[str, Any]:
    """JSON 整体回复（接口文档 8.7）。"""
    kp = _require_kp(db, kp_id)
    session = _get_session(session_id, kp_id)
    llm = get_llm()
    out = llm.tutor_chat(
        kp_name=kp.name, history=list(session["history"]), message=message
    )
    _append_turn(session, message, out["reply"])
    return {
        "sessionId": session["sessionId"],
        "reply": out["reply"],
        "suggestions": out["suggestions"],
    }


def _sse_block(payload: dict[str, Any], event: str | None = None) -> str:
    """SSE 块（15.4）：可选 event 行 + data 行 + 空行分隔。"""
    data = json.dumps(payload, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def sse_stream(
    db: Session, *, kp_id: str, session_id: str | None, message: str
) -> Iterator[str]:
    """SSE 流式回复（接口文档 15.4）。调用方需先确认 kp 存在（404 走 JSON 信封）。

    增量 token → data:{"delta":..}；正常收尾 → event: done {sessionId, suggestions}；
    生成异常 → event: error {code:2001,...}，已发 delta 由前端保留。
    """
    kp = _require_kp(db, kp_id)
    session = _get_session(session_id, kp_id)
    llm = get_llm()
    history = list(session["history"])

    def _events() -> Iterator[str]:
        parts: list[str] = []
        try:
            for delta in llm.tutor_chat_stream(
                kp_name=kp.name, history=history, message=message
            ):
                parts.append(delta)
                yield _sse_block({"delta": delta})
            reply = "".join(parts)
            _append_turn(session, message, reply)
            yield _sse_block(
                attach_generation_meta({
                    "sessionId": session["sessionId"],
                    "suggestions": llm.tutor_suggestions(message),
                }),
                event="done",
            )
        except LLMGenerationError as exc:
            yield _sse_block({"code": 2001, "message": str(exc)}, event="error")

    return _events()
