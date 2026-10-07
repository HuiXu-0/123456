"""对话式学习画像诊断编排（接口文档 17.1；C2 三段式重构）。

驱动 DialogueDiagnosticAgent 的三段式诊断状态机，并把每段产出的画像维度增量写入
StudentPortrait（覆盖/随学随新）。SSE / JSON 双模式与 8.7/15.4 苏格拉底辅导一脉
相承，复用同一套 _sse_block 流式封装，并新增 `event: interaction` 下发「引导式抛出」
的微测题 / 偏好选择题（前端渲染为可点选卡片）：

三段（会话内顺序推进，状态机记于进程内存 dict，TTL 30 分钟）：
  ① opening    采集主观维（学习目标 / 先验经验），自由文本；
  ② microtest  逐题抛出客观微测（复用 quiz 题库），按作答**行为反推能力**，段末据全部
               作答产出能力维 knowledge_base + 写 Mastery 低置信基线（能力靠测）；
  ③ preference 逐题抛出「二/三选一」偏好题，按选择**归类型**（不打分）。
三段走完 → diagnosisComplete=true。

- JSON 模式（17.1）：{sessionId, reply, portraitUpdates, suggestions, diagnosisComplete,
  interaction}（interaction 为本轮抛出的待作答交互，无则 null）；
- SSE 模式（17.1）：data:{delta} 逐句下发回复 → event: portrait 携维度增量 →
  event: interaction 携待作答交互 → event: done（sessionId/suggestions/diagnosisComplete）；
  生成异常 → event: error {code:2001,...}。

SSE 生成器在自有 SQLAlchemy 会话内完成「读画像 → Agent/微测 → 写画像/基线」（请求级
db 依赖在消费生成器前已关闭，故不能复用），保证流式期间 DB 可用。
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.generation_provenance import attach_generation_meta

from app.agents import dialogue_agent
from app.agents.memory.session_store import SessionStore, register_pool
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm import (
    LLMGenerationError,
    SUBJECTIVE_DIM_KEYS,
    _sanitize_portrait_updates,
)
from app.services import diagnostic_microtest, student_portrait
from app.services.learning_evidence import capture_shadow_event, from_diagnostic

# 会话 TTL：30 分钟（与 8.7 苏格拉底辅导同口径）
SESSION_TTL_SECONDS = 30 * 60

# sessionId → 三段式诊断会话状态
# R4.1：4 处分散的 _sessions 统一到 SessionStore（TTL / 惰性 GC / 取建语义不变，
# id 前缀仍为 d_；既有测试 _sessions[sid]["expiresAt"]=0.0 的戳法因 dict 兼容照旧可用）
_sessions: SessionStore = register_pool(
    "dialogue",
    SessionStore(ttl_seconds=SESSION_TTL_SECONDS, name="dialogue", id_prefix="d_"),
)

# 按句切分回复用于 SSE 逐条下发（保留句末标点，贴合 17.1「逐条下发追问语」）
_SENTENCE_RE = re.compile(r"[^。！？!?\n]*[。！？!?\n]|[^。！？!?\n]+")


def _gc() -> None:
    """惰性清理过期会话（访问时触发，无后台线程）。"""
    _sessions.gc()


def _get_session(session_id: str | None, user_id: str) -> dict[str, Any]:
    """取/建会话并续期。过期或未知 id → 以该 id（或新 d_ 前缀 id）重建空诊断状态。"""
    return _sessions.get_or_create(
        session_id,
        lambda sid: {
            "sessionId": sid,
            "userId": user_id,
            "history": [],
            "stage": dialogue_agent.STAGE_OPENING,
            "collected": set(),  # 本会话已采集维度 key（推进诊断收敛）
            "microtest": None,  # {queue, idx, results}（进入微测段时构建）
            "preference_idx": 0,
            "pending": None,  # 当前等待作答的交互（微测题 / 偏好题）
        },
    )


def _append_turn(session: dict[str, Any], message: str, reply: str) -> None:
    session["history"].append({"role": "user", "content": message})
    session["history"].append({"role": "assistant", "content": reply})


# ── 段间转移 ──────────────────────────────────────────────────────────────────

def _enter_microtest(db: Session, session: dict[str, Any]) -> tuple[dict | None, str]:
    """进入微测段：构建题队列，抛出首题（无题库则直接转偏好段，防御）。"""
    queue = diagnostic_microtest.select_microtest(db)
    session["microtest"] = {"queue": queue, "idx": 0, "results": []}
    session["stage"] = dialogue_agent.STAGE_MICROTEST
    if not queue:
        return _enter_preference(db, session, first=True)
    q0 = queue[0]
    session["pending"] = {"type": "quiz", **q0}
    interaction = dialogue_agent.quiz_interaction(session["sessionId"], 0, q0)
    reply = dialogue_agent.microtest_reply(ack_correct=None, question=q0)
    return interaction, reply


def _enter_preference(
    db: Session, session: dict[str, Any], *, first: bool
) -> tuple[dict | None, str]:
    """进入/推进偏好段：抛出当前 preference_idx 对应的偏好选择题。"""
    session["stage"] = dialogue_agent.STAGE_PREFERENCE
    idx = session["preference_idx"]
    dim_key = dialogue_agent.PREFERENCE_ORDER[idx]
    session["pending"] = {"type": "preference", "dimKey": dim_key}
    interaction = dialogue_agent.preference_interaction(session["sessionId"], idx, dim_key)
    reply = dialogue_agent.preference_reply(first=first, dim_key=dim_key)
    return interaction, reply


def _resolve_quiz_answer(pending: dict[str, Any], answer: str | None, message: str) -> str | None:
    """微测作答取值：优先 answer（option_id 直传）；否则把 message 当选项文本回映 option_id。"""
    if answer:
        return answer
    text = (message or "").strip()
    for opt in pending.get("options", []):
        if text and text == (opt.get("option_text") or "").strip():
            return opt.get("option_id")
    return message or None


# ── 单轮诊断状态机 ────────────────────────────────────────────────────────────

def _run_turn(
    db: Session,
    session: dict[str, Any],
    message: str,
    answer: str | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """执行一轮诊断：按当前段推进，产出回复 / 维度增量 / 待作答交互。"""
    stage = session["stage"]
    first_turn = not session["history"]
    updates: list[dict[str, Any]] = []
    interaction: dict[str, Any] | None = None
    suggestions: list[str] = []
    reply = ""

    if stage == dialogue_agent.STAGE_OPENING:
        collected_subj = session["collected"] & set(SUBJECTIVE_DIM_KEYS)
        target = dialogue_agent.next_opening_key(collected_subj) or "prior_experience"
        subj = dialogue_agent.extract_subjective(
            message=message, context=context, first_turn=first_turn, target_key=target
        )
        updates += subj
        session["collected"].update(u["key"] for u in subj)
        nxt = dialogue_agent.next_opening_key(session["collected"] & set(SUBJECTIVE_DIM_KEYS))
        if nxt is None:  # 主观维齐 → 转微测
            intro, _ = dialogue_agent.opening_reply(ack=bool(subj), next_key=None)
            interaction, mt_reply = _enter_microtest(db, session)
            reply = f"{intro} {mt_reply}"
        else:
            reply, suggestions = dialogue_agent.opening_reply(ack=bool(subj), next_key=nxt)

    elif stage == dialogue_agent.STAGE_MICROTEST:
        pend = session["pending"] or {}
        answer_value = _resolve_quiz_answer(pend, answer, message)
        correct = diagnostic_microtest.grade_answer(db, pend.get("questionId", ""), answer_value)
        mt = session["microtest"]
        mt["results"].append(
            {"kpId": pend.get("kpId"), "kpName": pend.get("kpName"), "correct": correct}
        )
        if correct is not None and pend.get("kpId") and pend.get("questionId"):
            capture_shadow_event(
                from_diagnostic(
                    user_id=session["userId"],
                    result={"kpId": pend["kpId"], "correct": correct},
                    source_id=(
                        f"diagnostic:{session['sessionId']}:{pend['questionId']}"
                    ),
                    occurred_at=datetime.now(timezone.utc),
                )
            )
        mt["idx"] += 1
        if mt["idx"] < len(mt["queue"]):
            q = mt["queue"][mt["idx"]]
            session["pending"] = {"type": "quiz", **q}
            interaction = dialogue_agent.quiz_interaction(session["sessionId"], mt["idx"], q)
            reply = dialogue_agent.microtest_reply(ack_correct=correct, question=q)
        else:  # 微测段结束 → 产出能力维 + 写 Mastery 低置信基线
            results = mt["results"]
            ability = diagnostic_microtest.build_ability_dimension(results)
            diagnostic_microtest.write_baseline(db, session["userId"], results)
            updates.append(ability)
            session["collected"].add("knowledge_base")
            session["preference_idx"] = 0
            interaction, reply = _enter_preference(db, session, first=True)

    elif stage == dialogue_agent.STAGE_PREFERENCE:
        pend = session["pending"] or {}
        dim_key = pend.get("dimKey", dialogue_agent.PREFERENCE_ORDER[0])
        answer_value = answer if answer else message
        pref = dialogue_agent.classify_preference(dim_key, answer_value or "")
        updates.append(pref)
        session["collected"].add(dim_key)
        session["preference_idx"] += 1
        if session["preference_idx"] < len(dialogue_agent.PREFERENCE_ORDER):
            interaction, reply = _enter_preference(db, session, first=False)
        else:
            session["stage"] = dialogue_agent.STAGE_DONE
            reply = dialogue_agent.closing_reply()
            suggestions = ["生成学习路径"]

    else:  # STAGE_DONE：诊断已完成，回收尾语，不再产出
        reply = dialogue_agent.closing_reply()
        suggestions = ["生成学习路径"]

    # 契约清洗（统一附 kind/label、剥离非法 score、归一 confidence/source/optionKey/basis）
    clean = _sanitize_portrait_updates(updates)
    if clean:
        student_portrait.apply_updates(db, session["userId"], clean)
    _append_turn(session, message, reply)
    return {
        "reply": reply,
        "updates": clean,
        "suggestions": suggestions,
        "interaction": interaction,
        "diagnosisComplete": session["stage"] == dialogue_agent.STAGE_DONE,
    }


# ── JSON / SSE 对外入口 ───────────────────────────────────────────────────────

def dialogue(
    db: Session,
    *,
    user_id: str,
    session_id: str | None,
    message: str,
    answer: str | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """JSON 整体回复（接口文档 17.1 非流式）。"""
    session = _get_session(session_id, user_id)
    result = _run_turn(db, session, message, answer, context)
    return {
        "sessionId": session["sessionId"],
        "reply": result["reply"],
        "portraitUpdates": result["updates"],
        "suggestions": result["suggestions"],
        "diagnosisComplete": result["diagnosisComplete"],
        "interaction": result["interaction"],
    }


def _sse_block(payload: dict[str, Any], event: str | None = None) -> str:
    """SSE 块（17.1/15.4）：可选 event 行 + data 行 + 空行分隔。"""
    data = json.dumps(payload, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def _chunk_reply(reply: str) -> list[str]:
    """按句切分回复供 delta 逐条下发；空回复兜底为单块。"""
    chunks = [m.group(0) for m in _SENTENCE_RE.finditer(reply) if m.group(0).strip()]
    return chunks or [reply]


def sse_stream(
    *,
    user_id: str,
    session_id: str | None,
    message: str,
    answer: str | None,
    context: dict[str, Any] | None,
) -> Iterator[str]:
    """SSE 流式回复（接口文档 17.1）。

    逐句 delta → event: portrait（本轮维度增量，有则发）→ event: interaction（本轮抛出
    的待作答交互，有则发）→ event: done（sessionId/suggestions/diagnosisComplete）；
    生成异常 → event: error {code:2001,...}。
    """
    session = _get_session(session_id, user_id)

    def _events() -> Iterator[str]:
        db = SessionLocal()
        try:
            result = _run_turn(db, session, message, answer, context)
            delay = settings.tutor_stream_delay_ms / 1000
            for chunk in _chunk_reply(result["reply"]):
                if delay > 0:
                    time.sleep(delay)
                yield _sse_block({"delta": chunk})
            if result["updates"]:
                yield _sse_block({"updates": result["updates"]}, event="portrait")
            if result["interaction"]:
                yield _sse_block(result["interaction"], event="interaction")
            yield _sse_block(
                attach_generation_meta({
                    "sessionId": session["sessionId"],
                    "suggestions": result["suggestions"],
                    "diagnosisComplete": result["diagnosisComplete"],
                }),
                event="done",
            )
        except LLMGenerationError as exc:
            yield _sse_block({"code": 2001, "message": str(exc)}, event="error")
        finally:
            db.close()

    return _events()
