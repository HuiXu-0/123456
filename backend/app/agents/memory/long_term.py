"""长期记忆（R4.1）——既有 SQLite 数据的**只读标准化投影**。

## 设计边界（本模块刻意"不做什么"）

| 不做 | 原因 |
|---|---|
| ❌ 不建新表 / 不 ALTER 表 | 改表结构是明确禁止项；语义检索需要向量列，留作后续单独评审 |
| ❌ 不暴露 Session 给调用方 | ``Agent 不得直接操作 SQLite Session`` —— Session 由本类**自行开合** |
| ❌ 不重写掌握度 / 画像算法 | 只读既有行，口径以表为准；派生算法仍在 ``services/*`` |
| ❌ 不做 LLM 召回 | 零网络、无 Key 可跑（mock-first） |

## 6 个数据源 → ``Evidence.kind``

| kind | 表 / 实体 | subject | 说明 |
|---|---|---|---|
| ``portrait`` | ``StudentPortrait``（经 ``services.student_portrait``） | 维度 ``key`` | **复用既有 service**（它是派生/归一逻辑） |
| ``mastery`` | ``mastery`` | ``kp_id`` | 直读行，含 ``status/score/confidence/score_source`` |
| ``step`` | ``learning_step_progress`` | ``{kp_id}:{step}`` | 6 步过程完成标记 |
| ``note`` | ``learning_notes`` | ``kp_id`` | 康奈尔三栏笔记 |
| ``quiz`` | ``quiz_attempts`` | ``kp_id`` | 测验作答快照 |
| ``event`` | ``learning_events`` | ``knowledge_id`` | 追加式学习事件（实体名是 ``LearningEventRecord``） |

> 除 portrait 外均为**纯表读取**，不含业务算法，故不构成"重写已有能力"。

## 检索语义

``query`` 是自由文本 → **确定性关键词过滤**（见 ``Evidence.matches``）。
空 query 返回全量（受 ``limit`` 与每源上限约束）。顺序固定：
portrait → mastery → step → note → quiz → event（可复现，便于断言）。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from app.agents.memory.interface import Evidence
from app.core.database import SessionLocal
from app.models.entities import (
    LearningEventRecord,
    LearningNote,
    LearningStepProgress,
    Mastery,
    QuizAttempt,
)
from app.services import student_portrait as portrait_service

DEFAULT_LIMIT = 50
# 单个数据源的取数上限：防止极端账号把 query 撑爆（总量再由 DEFAULT_LIMIT 收口）
_PER_SOURCE_LIMIT = 100

# 证据出现的固定顺序（稳定可复现）
_KIND_ORDER: tuple[str, ...] = ("portrait", "mastery", "step", "note", "quiz", "event")

_TERM_SPLIT = re.compile(r"[\s,，、;；]+")


def split_terms(query: str) -> list[str]:
    """把自由文本切成检索词（空白 / 中英文标点分隔，去空）。"""
    return [term for term in _TERM_SPLIT.split((query or "").strip()) if term]


def _clamp01(value: Any, default: float = 1.0) -> float:
    """置信度收敛到 [0,1]；非数值 → default（不臆造）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0.0, min(1.0, float(value)))


def _iso(value: Any) -> str | None:
    """datetime → ISO-8601；已是字符串则原样；其余 → None。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return None


class LongTermMemory:
    """长期记忆读取器：每次查询自开自关 Session，**对外不暴露任何 Session**。

    Args:
        session_factory: Session 工厂（测试注入用）；缺省用 ``SessionLocal``。
        limit: 单次 ``query`` 的返回上限。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        # R4.5.2：与 AgentMemory 同一原则 —— 依赖 truthiness 的对象选择一律改成
        # 显式 None 判断（可调用对象虽通常为真，但 truthiness 分支是隐患来源）。
        self._session_factory = SessionLocal if session_factory is None else session_factory
        self._limit = int(limit)

    @property
    def limit(self) -> int:
        return self._limit

    def query(
        self, user_id: str, query: str = "", *, limit: int | None = None
    ) -> list[Evidence]:
        """读取某用户的长期证据。

        Args:
            user_id: 归属用户（隔离键，空 → 直接返回空，**绝不跨用户**）。
            query: 自由文本检索词；空 → 全量。
            limit: 覆盖默认上限。

        Returns:
            标准化 ``Evidence`` 列表（可 JSON 序列化，可进 LangGraph State）。
        """
        uid = (user_id or "").strip()
        if not uid:
            return []
        cap = int(limit) if limit else self._limit
        if cap <= 0:
            return []

        db = self._session_factory()
        try:
            collected = self._collect(db, uid)
        finally:
            db.close()

        terms = split_terms(query)
        if terms:
            collected = [item for item in collected if item.matches(terms)]
        return collected[:cap]

    # ── 各数据源 ──────────────────────────────────────────────────────────────

    def _collect(self, db: Session, user_id: str) -> list[Evidence]:
        buckets: dict[str, list[Evidence]] = {
            "portrait": self._from_portrait(db, user_id),
            "mastery": self._from_mastery(db, user_id),
            "step": self._from_steps(db, user_id),
            "note": self._from_notes(db, user_id),
            "quiz": self._from_quizzes(db, user_id),
            "event": self._from_events(db, user_id),
        }
        return [item for kind in _KIND_ORDER for item in buckets[kind]]

    def _from_portrait(self, db: Session, user_id: str) -> list[Evidence]:
        """画像维度 —— **复用 ``services.student_portrait``**（它是派生/归一逻辑）。"""
        portrait = portrait_service.get_portrait(db, user_id)
        dimensions = portrait.get("dimensions") or []
        evidence: list[Evidence] = []
        for dim in dimensions[:_PER_SOURCE_LIMIT]:
            if not isinstance(dim, dict):
                continue
            key = str(dim.get("key") or "")
            label = str(dim.get("label") or key)
            score = dim.get("score")
            value = dim.get("value")
            display = str(value) if value not in (None, "") else (
                f"{score}分" if isinstance(score, (int, float)) else ""
            )
            summary = f"画像 {label}：{display}" if display else f"画像 {label}"
            evidence.append(
                Evidence(
                    kind="portrait",
                    user_id=user_id,
                    subject=key,
                    summary=summary,
                    value={k: v for k, v in dim.items()},
                    observed_at=_iso(dim.get("updatedAt") or portrait.get("updatedAt")),
                    confidence=_clamp01(dim.get("confidence")),
                )
            )
        return evidence

    def _from_mastery(self, db: Session, user_id: str) -> list[Evidence]:
        """掌握度 —— 直读 ``mastery`` 行（纯读取，无算法）。"""
        rows = (
            db.query(Mastery)
            .filter(Mastery.user_id == user_id)
            .order_by(Mastery.kp_id.asc())
            .limit(_PER_SOURCE_LIMIT)
            .all()
        )
        evidence: list[Evidence] = []
        for row in rows:
            score = row.score
            score_text = f"，能力分 {score}" if score is not None else ""
            source = f"（{row.score_source}）" if row.score_source else ""
            evidence.append(
                Evidence(
                    kind="mastery",
                    user_id=user_id,
                    subject=row.kp_id,
                    summary=f"掌握度 {row.kp_id}：{row.status}{score_text}{source}",
                    value={
                        "kpId": row.kp_id,
                        "status": row.status,
                        "score": score,
                        "scoreSource": row.score_source,
                    },
                    observed_at=None,
                    confidence=_clamp01(row.confidence),
                )
            )
        return evidence

    def _from_steps(self, db: Session, user_id: str) -> list[Evidence]:
        """学习步骤完成标记（接口文档 18.3）。"""
        rows = (
            db.query(LearningStepProgress)
            .filter(LearningStepProgress.user_id == user_id)
            .order_by(LearningStepProgress.updated_at.desc())
            .limit(_PER_SOURCE_LIMIT)
            .all()
        )
        return [
            Evidence(
                kind="step",
                user_id=user_id,
                subject=f"{row.kp_id}:{row.step}",
                summary=f"学习步骤 {row.kp_id}/{row.step}："
                f"{'已完成' if row.done else '未完成'}",
                value={"kpId": row.kp_id, "step": row.step, "done": row.done},
                observed_at=_iso(row.updated_at),
            )
            for row in rows
        ]

    def _from_notes(self, db: Session, user_id: str) -> list[Evidence]:
        """康奈尔笔记（接口文档 18.4/18.5）。"""
        rows = (
            db.query(LearningNote)
            .filter(LearningNote.user_id == user_id)
            .order_by(LearningNote.updated_at.desc())
            .limit(_PER_SOURCE_LIMIT)
            .all()
        )
        evidence: list[Evidence] = []
        for row in rows:
            filled = bool(row.main_notes or row.summary or row.cue_notes)
            preview = (row.summary or row.main_notes or "").strip().replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:60] + "…"
            parts = ["笔记", row.kp_id, "已填写" if filled else "未填写"]
            if preview:
                parts.append(preview)
            evidence.append(
                Evidence(
                    kind="note",
                    user_id=user_id,
                    subject=row.kp_id,
                    summary=" · ".join(parts),
                    value={
                        "kpId": row.kp_id,
                        "hasCues": bool(row.cue_notes),
                        "hasMain": bool(row.main_notes),
                        "hasSummary": bool(row.summary),
                        "preview": preview,
                    },
                    observed_at=_iso(row.updated_at),
                )
            )
        return evidence

    def _from_quizzes(self, db: Session, user_id: str) -> list[Evidence]:
        """测验作答历史（C-fix 批3 行为数据层）。"""
        rows = (
            db.query(QuizAttempt)
            .filter(QuizAttempt.user_id == user_id)
            .order_by(QuizAttempt.created_at.desc())
            .limit(_PER_SOURCE_LIMIT)
            .all()
        )
        return [
            Evidence(
                kind="quiz",
                user_id=user_id,
                subject=row.kp_id,
                summary=f"测验 {row.kp_id}：{row.score}分 "
                f"{'通过' if row.passed else '未通过'}"
                f"（答对 {row.correct_count}/{row.total}）",
                value={
                    "kpId": row.kp_id,
                    "score": row.score,
                    "correctCount": row.correct_count,
                    "total": row.total,
                    "passed": row.passed,
                },
                observed_at=_iso(row.created_at),
            )
            for row in rows
        ]

    def _from_events(self, db: Session, user_id: str) -> list[Evidence]:
        """追加式学习事件（实体名 ``LearningEventRecord``，表 ``learning_events``）。"""
        rows = (
            db.query(LearningEventRecord)
            .filter(LearningEventRecord.user_id == user_id)
            .order_by(LearningEventRecord.timestamp.desc())
            .limit(_PER_SOURCE_LIMIT)
            .all()
        )
        evidence: list[Evidence] = []
        for row in rows:
            evidence.append(
                Evidence(
                    kind="event",
                    user_id=user_id,
                    subject=row.knowledge_id,
                    summary=f"学习事件 {row.event_type}/{row.knowledge_id}"
                    f"（score={row.score:.2f}）",
                    value={
                        "knowledgeId": row.knowledge_id,
                        "eventType": row.event_type,
                        "sourceType": row.source_type,
                        "score": row.score,
                    },
                    observed_at=_iso(row.timestamp),
                )
            )
        return evidence

    # ── 便捷查询 ──────────────────────────────────────────────────────────────

    def kinds(self) -> tuple[str, ...]:
        """本记忆层支持的证据类型。"""
        return _KIND_ORDER

    def collect_all(self, user_id: str, *, limit: int | None = None) -> list[Evidence]:
        """等价于 ``query(user_id, "")`` —— 全量读取。"""
        return self.query(user_id, "", limit=limit)

    def group_by_kind(
        self, user_id: str, query: str = "", *, limit: int | None = None
    ) -> dict[str, list[Evidence]]:
        """按 kind 分组（供上层/测试按类型消费）。"""
        grouped: dict[str, list[Evidence]] = {kind: [] for kind in _KIND_ORDER}
        for item in self.query(user_id, query, limit=limit):
            grouped.setdefault(item.kind, []).append(item)
        return grouped


def as_dicts(items: Iterable[Evidence]) -> list[dict[str, Any]]:
    """Evidence → 普通 dict（喂给 LLM / 写日志 / 进 State 均可）。"""
    return [item.model_dump(mode="json") for item in items]
