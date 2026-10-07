"""学习流程服务（接口文档第 18 章，C2）——费曼 + 康奈尔学习闭环。

覆盖：
- 18.1 cornell_cues：康奈尔线索（cues 5-8 + noteOutline + summaryHint + sources），
  经 LLMClient.generate_cornell_cues 双模式生成（mock 确定性 / deepseek 真实）。
- 18.2 feynman（SSE/JSON 双模式）：复用费曼 Agent 评估讲解 → 点评 + gaps + 追问；
  gaps 的 review[] 在本层按 kp 注入真实资源端点（复用 6.3 resources[] 结构）。
  会话上下文进程内存 TTL 30 分钟（与 8.7/17.1 同口径），SSE 与 17.1 同体例。
- 18.3 steps：6 步过程标记读写（LearningStepProgress 轻量表），mastery 字段透传 7.1。
- 18.4/18.5 notes：康奈尔三栏笔记读写（LearningNote 轻量表，整体覆盖保存）。

「已掌握」不在本模块判定——复用 9.1 测验提交 → 7.3 Mastery（契约 18.6），
本层只在 steps 响应中透传 7.1 掌握态供前端徽标。两张新表无外键耦合，不改
Journey/Mastery 表。
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents import feynman_agent
from app.agents.memory.session_store import SessionStore, register_pool
from app.core.generation_provenance import attach_generation_meta
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm import LECTURE_DIFFICULTIES, get_llm
from app.models.entities import KnowledgePoint, LearningNote, LearningStepProgress
from app.services import mastery as mastery_service
from app.services.learning_evidence import (
    capture_shadow_event,
    from_feynman,
    from_learning_step,
)

# 学习步骤固定 6 项（接口文档 18.3，视频/讲义/图解/笔记/费曼/实操）
STEP_KEYS: list[str] = ["video", "lecture", "diagram", "note", "feynman", "practice"]
_STEP_SET: set[str] = set(STEP_KEYS)

# 费曼会话 TTL：30 分钟（与 8.7 苏格拉底辅导 / 17.1 对话诊断同口径）
SESSION_TTL_SECONDS = 30 * 60

# sessionId → {sessionId, kpId, history:[{role,content}], expiresAt(monotonic)}
# R4.1：4 处分散的 _sessions 统一到 SessionStore（TTL / 惰性 GC / 取建语义不变，
# id 前缀仍为 f_；既有测试 _sessions[sid]["expiresAt"] 的戳法因 dict 兼容照旧可用）
_sessions: SessionStore = register_pool(
    "feynman",
    SessionStore(ttl_seconds=SESSION_TTL_SECONDS, name="feynman", id_prefix="f_"),
)

# 按句切分点评供 SSE 逐条 delta 下发（与 17.1 对话诊断同口径）
_SENTENCE_RE = re.compile(r"[^。！？!?]*[。！？!?]|[^。！？!?]+")


class UnknownKnowledgePoint(Exception):
    """知识点不存在（→ code 1004 / 404）。"""


class InvalidDifficulty(Exception):
    """难度档非法（→ code 1001 / 400）。"""


class InvalidStep(Exception):
    """学习步骤非法（不在 6 项内 → code 1001 / 400）。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_kp(db: Session, kp_id: str) -> KnowledgePoint:
    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise UnknownKnowledgePoint(kp_id)
    return kp


# ---- 18.1 康奈尔线索生成 -------------------------------------------------------
def cornell_cues(db: Session, *, kp_id: str, difficulty: str) -> dict[str, Any]:
    """康奈尔线索生成（接口文档 18.1）。"""
    kp = _require_kp(db, kp_id)
    if difficulty not in LECTURE_DIFFICULTIES:
        raise InvalidDifficulty(difficulty)
    generated = get_llm().generate_cornell_cues(
        kp.id, kp.name, difficulty, kp.description or ""
    )
    return {
        "kpId": kp.id,
        "difficulty": difficulty,
        "cues": generated["cues"],
        "noteOutline": generated["noteOutline"],
        "summaryHint": generated["summaryHint"],
        "sources": generated["sources"],
    }


# ---- 18.2 费曼讲解评估（SSE / JSON 双模式） ------------------------------------
def _gc() -> None:
    """惰性清理过期会话（访问时触发，无后台线程）。"""
    _sessions.gc()


def _get_session(session_id: str | None, kp_id: str) -> dict[str, Any]:
    """取/建会话并续期。过期或未知 id → 以该 id（或新 f_ 前缀 id）重建空上下文。"""
    return _sessions.get_or_create(
        session_id,
        lambda sid: {"sessionId": sid, "kpId": kp_id, "history": []},
    )


def _append_turn(session: dict[str, Any], explanation: str, feedback: str) -> None:
    session["history"].append({"role": "user", "content": explanation})
    session["history"].append({"role": "assistant", "content": feedback})


def _review_resources(
    kp_id: str, kp_name: str, difficulty: str = "初级"
) -> list[dict[str, Any]]:
    """缺口「应回看资源」（接口文档 18.2 review[]，复用 6.3 resources[] 结构）。

    指向真实资源端点（8.2 讲义 / 8.5 图解 / 8.3 视频），前端可点开。
    """
    return [
        {"kind": "lecture", "title": f"《{kp_name}》自适应讲义", "kpId": kp_id,
         "endpoint": "/resource/lecture", "difficulty": difficulty},
        {"kind": "diagram", "title": f"{kp_name} · 知识图解", "kpId": kp_id,
         "endpoint": "/resource/diagram"},
        {"kind": "video", "title": f"{kp_name} · 讲解视频", "kpId": kp_id,
         "endpoint": "/resource/video", "difficulty": difficulty},
    ]


def _enrich_gaps(db: Session, gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为每个 gap 编号并注入 review[]（按 gap.kpId 查知识点名 → 真实资源端点）。"""
    name_cache: dict[str, str] = {}
    enriched: list[dict[str, Any]] = []
    for i, g in enumerate(gaps):
        gk = g.get("kpId")
        if gk and gk not in name_cache:
            kp = db.get(KnowledgePoint, gk)
            name_cache[gk] = kp.name if kp is not None else gk
        review = _review_resources(gk, name_cache.get(gk, gk)) if gk else []
        enriched.append({
            "id": f"g{i + 1}",
            "kpId": gk,
            "title": g["title"],
            "detail": g["detail"],
            "severity": g["severity"],
            "review": review,
        })
    return enriched


def _eval_turn(
    db: Session,
    session: dict[str, Any],
    kp: KnowledgePoint,
    explanation: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """单轮费曼评估：Agent 评估 → 注入 gaps.review → 记会话历史。"""
    turn_number = len(session["history"]) // 2 + 1
    result = feynman_agent.respond(
        kp_id=kp.id,
        kp_name=kp.name,
        description=kp.description or "",
        history=list(session["history"]),
        explanation=explanation,
    )
    gaps = _enrich_gaps(db, result["gaps"])
    _append_turn(session, explanation, result["feedback"])
    if user_id is not None:
        capture_shadow_event(
            from_feynman(
                user_id=user_id,
                knowledge_id=kp.id,
                result=result,
                source_id=f"feynman:{session['sessionId']}:turn-{turn_number}",
                occurred_at=_now(),
            )
        )
    return {
        "feedback": result["feedback"],
        "gaps": gaps,
        "score": result["score"],
        "followups": result["followups"],
        "complete": result["complete"],
    }


def feynman_chat(
    db: Session,
    *,
    kp_id: str,
    session_id: str | None,
    explanation: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """JSON 整体评估（接口文档 18.2 非流式）。"""
    kp = _require_kp(db, kp_id)
    session = _get_session(session_id, kp_id)
    result = _eval_turn(db, session, kp, explanation, user_id=user_id)
    return {
        "sessionId": session["sessionId"],
        "feedback": result["feedback"],
        "score": result["score"],
        "gaps": result["gaps"],
        "followups": result["followups"],
        "complete": result["complete"],
    }


def _sse_block(payload: dict[str, Any], event: str | None = None) -> str:
    """SSE 块（18.2/17.1/15.4）：可选 event 行 + data 行 + 空行分隔。"""
    data = json.dumps(payload, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def _chunk_feedback(feedback: str) -> list[str]:
    """按句切分点评供 delta 逐条下发；空回复兜底为单块。"""
    chunks = [m.group(0) for m in _SENTENCE_RE.finditer(feedback) if m.group(0).strip()]
    return chunks or [feedback]


def feynman_sse_stream(
    db: Session,
    *,
    kp_id: str,
    session_id: str | None,
    explanation: str,
    user_id: str | None = None,
) -> Iterator[str]:
    """SSE 流式评估（接口文档 18.2）。调用方需先确认 kp 存在（404 走 JSON 信封）。

    逐句 delta 点评 → event: gaps（本轮缺口，有则发）→ event: done
    （sessionId/score/followups/complete）；生成异常 → event: error {code:2001,...}。
    评估在生成器内自有 SessionLocal 完成（请求级 db 在流消费前已关闭，与 17.1 同）。
    """
    _require_kp(db, kp_id)  # 同步 404（路由 try/except 捕获 → JSON 信封）
    session = _get_session(session_id, kp_id)

    def _events() -> Iterator[str]:
        from app.core.llm import LLMGenerationError

        inner = SessionLocal()
        try:
            kp = _require_kp(inner, kp_id)
            result = _eval_turn(
                inner, session, kp, explanation, user_id=user_id
            )
            delay = settings.tutor_stream_delay_ms / 1000
            for chunk in _chunk_feedback(result["feedback"]):
                if delay > 0:
                    time.sleep(delay)
                yield _sse_block({"delta": chunk})
            if result["gaps"]:
                yield _sse_block({"gaps": result["gaps"]}, event="gaps")
            yield _sse_block(
                attach_generation_meta({
                    "sessionId": session["sessionId"],
                    "score": result["score"],
                    "followups": result["followups"],
                    "complete": result["complete"],
                }),
                event="done",
            )
        except LLMGenerationError as exc:
            yield _sse_block({"code": 2001, "message": str(exc)}, event="error")
        finally:
            inner.close()

    return _events()


# ---- 18.3 学习步骤进度 --------------------------------------------------------
def get_steps(db: Session, *, user_id: str, kp_id: str) -> dict[str, Any]:
    """获取学习步骤进度（接口文档 18.3）。mastery 透传 7.1 该 kp 掌握态。"""
    _require_kp(db, kp_id)
    rows = (
        db.query(LearningStepProgress)
        .filter(
            LearningStepProgress.user_id == user_id,
            LearningStepProgress.kp_id == kp_id,
        )
        .all()
    )
    done_map = {r.step: r.done for r in rows}
    steps = {k: bool(done_map.get(k, False)) for k in STEP_KEYS}
    # 透传 7.1 掌握态（未出现默认 learning，与 8.1 知识点元信息同口径）；"已掌握" 以此为准
    mastery = mastery_service.get_status_map(db, user_id).get(
        kp_id, mastery_service.STATUS_LEARNING
    )
    updated_at = max((r.updated_at for r in rows), default=None)
    return {
        "kpId": kp_id,
        "steps": steps,
        "mastery": mastery,
        "completedCount": sum(1 for v in steps.values() if v),
        "total": len(STEP_KEYS),
        "updatedAt": updated_at.isoformat() if updated_at else None,
    }


def set_step(
    db: Session, *, user_id: str, kp_id: str, step: str, done: bool
) -> dict[str, Any]:
    """更新学习步骤进度（接口文档 18.3，幂等）。返回最新整体进度。"""
    _require_kp(db, kp_id)
    if step not in _STEP_SET:
        raise InvalidStep(step)
    row = (
        db.query(LearningStepProgress)
        .filter(
            LearningStepProgress.user_id == user_id,
            LearningStepProgress.kp_id == kp_id,
            LearningStepProgress.step == step,
        )
        .one_or_none()
    )
    if row is None:
        row = LearningStepProgress(
            user_id=user_id, kp_id=kp_id, step=step, done=bool(done)
        )
        db.add(row)
    else:
        row.done = bool(done)
        row.updated_at = _now()
    db.commit()
    db.refresh(row)
    if row.done:
        capture_shadow_event(from_learning_step(row))
    return get_steps(db, user_id=user_id, kp_id=kp_id)


# ---- 18.4 / 18.5 康奈尔笔记 ---------------------------------------------------
def _get_or_create_note(db: Session, user_id: str, kp_id: str) -> LearningNote:
    """取/建笔记行（幂等）。首次访问落空笔记（updatedAt 即创建时间，契约 18.4）。"""
    row = (
        db.query(LearningNote)
        .filter(LearningNote.user_id == user_id, LearningNote.kp_id == kp_id)
        .one_or_none()
    )
    if row is None:
        row = LearningNote(
            user_id=user_id, kp_id=kp_id, cue_notes=[], main_notes="", summary=""
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _serialize_note(row: LearningNote) -> dict[str, Any]:
    """LearningNote → 接口文档 18.4 CornellNote。"""
    return {
        "kpId": row.kp_id,
        "cueNotes": list(row.cue_notes or []),
        "mainNotes": row.main_notes or "",
        "summary": row.summary or "",
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_note(db: Session, *, user_id: str, kp_id: str) -> dict[str, Any]:
    """获取康奈尔笔记（接口文档 18.4）。无数据 → 空笔记占位，不视为错误。"""
    _require_kp(db, kp_id)
    return _serialize_note(_get_or_create_note(db, user_id, kp_id))


def _normalize_cue_notes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """清洗 cueNotes：仅保留 {cueId, text} 形态。"""
    out: list[dict[str, Any]] = []
    for it in items or []:
        if isinstance(it, dict):
            out.append({"cueId": it.get("cueId"), "text": str(it.get("text") or "")})
    return out


def save_note(
    db: Session,
    *,
    user_id: str,
    kp_id: str,
    cue_notes: list[dict[str, Any]],
    main_notes: str,
    summary: str,
) -> dict[str, Any]:
    """保存康奈尔笔记（接口文档 18.5，整体覆盖，幂等）。"""
    _require_kp(db, kp_id)
    norm_cues = _normalize_cue_notes(cue_notes)
    row = (
        db.query(LearningNote)
        .filter(LearningNote.user_id == user_id, LearningNote.kp_id == kp_id)
        .one_or_none()
    )
    if row is None:
        row = LearningNote(
            user_id=user_id,
            kp_id=kp_id,
            cue_notes=norm_cues,
            main_notes=main_notes or "",
            summary=summary or "",
        )
        db.add(row)
    else:
        row.cue_notes = norm_cues
        row.main_notes = main_notes or ""
        row.summary = summary or ""
        row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _serialize_note(row)
