"""掌握度与学习旅程服务（B2-b）。

覆盖接口文档第 7 章：
- 7.1 get_status_map：Record<kpId, KPStatus>（未出现的知识点视为未开始）。
- 7.2 mark_check：learning → pending-check（已 passed 保持不变）。
- 7.3 mark_pass：置 passed（幂等）。通常由测验 ≥60 分联动触发（见 9.1）。
- 7.4 derive_current_step：按规则推导 currentStep（diagnose|generate-path|learn|review）。

后端为掌握度权威数据源（前端 Zustand 退化为缓存）。状态枚举严格为
`learning | pending-check | passed`（连字符，禁止下划线）——见接口文档 2.2。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import Journey, Mastery

# 掌握状态枚举（接口文档 2.2，连字符形式，全局唯一来源）
STATUS_LEARNING = "learning"
STATUS_PENDING_CHECK = "pending-check"
STATUS_PASSED = "passed"


def get_status_map(db: Session, user_id: str) -> dict[str, str]:
    """掌握度全集（接口文档 7.1）。仅返回已存在的行；未出现 = 未开始。"""
    rows = db.query(Mastery).filter(Mastery.user_id == user_id).all()
    return {r.kp_id: r.status for r in rows}


def _current_status(db: Session, user_id: str, kp_id: str) -> str | None:
    row = (
        db.query(Mastery)
        .filter(Mastery.user_id == user_id, Mastery.kp_id == kp_id)
        .one_or_none()
    )
    return row.status if row else None


def set_status(db: Session, user_id: str, kp_id: str, status: str) -> str:
    """写入/更新掌握状态（建行或改行），提交并返回最终状态。"""
    row = (
        db.query(Mastery)
        .filter(Mastery.user_id == user_id, Mastery.kp_id == kp_id)
        .one_or_none()
    )
    if row is None:
        row = Mastery(user_id=user_id, kp_id=kp_id, status=status)
        db.add(row)
    else:
        row.status = status
    db.commit()
    return status


def ensure_learning(db: Session, user_id: str, kp_id: str) -> str:
    """真实学习行为（如生成讲义）触发：未开始 → learning；已有状态保持不变。

    若该知识点此前仅有「诊断微测基线」（score_source=diagnostic，非真实学习），此刻真实
    学习开始 → 清除诊断标记（保留能力分），使其从"未开始"提升为"学习中"（路径据此显示
    in_progress）。这保证「微测答对 ≠ 学完/在学」与「真实学习」严格解耦。
    """
    row = _get_row(db, user_id, kp_id)
    if row is None:
        return set_status(db, user_id, kp_id, STATUS_LEARNING)
    if row.status == STATUS_PASSED:
        return STATUS_PASSED
    if row.score_source == "diagnostic":  # 真实学习开始 → 提升（清诊断标记，保留能力分）
        row.score_source = None
        if row.status not in (STATUS_LEARNING, STATUS_PENDING_CHECK):
            row.status = STATUS_LEARNING
        db.commit()
    return row.status


def learning_step(status: str | None, source: str | None) -> tuple[str, int]:
    """掌握状态 → 学习路径节点展示状态/进度（接口文档 2.3 status/progress）。

    **「已完成」只来自真实学习行为**（通过阶段测试 → status=passed）；诊断微测基线
    （source=diagnostic，未真实学习）一律视为「未开始」（pending），绝不显示已完成/进行中
    ——保证零基础新用户路径无任何「已完成」，且「微测≠学完」与学习进度解耦。
    """
    if status == STATUS_PASSED:
        return ("completed", 100)
    # 仅诊断基线（未真实学习）→ 未开始；真实学习中/待检验才 in_progress
    if status in (STATUS_LEARNING, STATUS_PENDING_CHECK) and source != "diagnostic":
        return ("in_progress", 70 if status == STATUS_PENDING_CHECK else 40)
    return ("pending", 0)


def mark_check(db: Session, user_id: str, kp_id: str) -> str:
    """去检验（接口文档 7.2）：learning（及未开始）→ pending-check；passed 保持不变。"""
    if _current_status(db, user_id, kp_id) == STATUS_PASSED:
        return STATUS_PASSED
    return set_status(db, user_id, kp_id, STATUS_PENDING_CHECK)


def mark_pass(db: Session, user_id: str, kp_id: str) -> str:
    """标记通过（接口文档 7.3，幂等）：置 passed。"""
    return set_status(db, user_id, kp_id, STATUS_PASSED)


def _get_row(db: Session, user_id: str, kp_id: str) -> Mastery | None:
    return (
        db.query(Mastery)
        .filter(Mastery.user_id == user_id, Mastery.kp_id == kp_id)
        .one_or_none()
    )


def set_baseline(
    db: Session,
    user_id: str,
    kp_id: str,
    *,
    score: int,
    confidence: float,
    source: str = "diagnostic",
) -> None:
    """写低置信能力基线（C2 口径统一）。source=diagnostic（诊断微测）/manual（简历/手动自陈）。

    仅写分数/置信/来源，**不置 passed**（不虚增知识图谱覆盖率）；新行落 learning，
    已有状态（含 passed）不回退——真实 quiz 已通过的能力不被基线覆盖。
    """
    row = _get_row(db, user_id, kp_id)
    if row is None:
        row = Mastery(user_id=user_id, kp_id=kp_id, status=STATUS_LEARNING)
        db.add(row)
    row.score = max(0, min(100, int(score)))
    row.confidence = max(0.0, min(1.0, float(confidence)))
    row.score_source = source
    db.commit()


def set_score(
    db: Session, user_id: str, kp_id: str, *, score: int, confidence: float = 0.85
) -> None:
    """真实 quiz 写高置信能力分（C2 口径统一，source=quiz），覆盖诊断基线同一行。

    仅更新能力分，不动 status（status 由 9.1 判分 ≥60 另行 mark_pass）。行不存在则建
    （理论上 quiz 前已有学习行；防御性建行 learning）。
    """
    row = _get_row(db, user_id, kp_id)
    if row is None:
        row = Mastery(user_id=user_id, kp_id=kp_id, status=STATUS_LEARNING)
        db.add(row)
    row.score = max(0, min(100, int(score)))
    row.confidence = max(0.0, min(1.0, float(confidence)))
    row.score_source = "quiz"
    db.commit()


def get_score_map(db: Session, user_id: str) -> dict[str, dict]:
    """各知识点能力分全集：{kpId: {score, confidence, source, status}}（None=未测）。

    4.4 能力雷达 / 学情概览能力维据此渲染；未出现的知识点视为未测（不臆造）。
    """
    rows = db.query(Mastery).filter(Mastery.user_id == user_id).all()
    return {
        r.kp_id: {
            "score": r.score,
            "confidence": r.confidence,
            "source": r.score_source,
            "status": r.status,
        }
        for r in rows
    }


def derive_current_step(
    db: Session, journey: Journey | None, status_map: dict[str, str]
) -> str:
    """推导旅程当前步骤（接口文档 7.4，与前端 getJourneyStep 一致）。

    未诊断 → diagnose；已诊断未生成路径 → generate-path；
    全部核心知识点 passed → review；否则 learn。
    （「全部课程完成」以核心知识点全 passed 推导——前端学习路径/图谱均由掌握度派生。）
    """
    if journey is None or not journey.has_diagnosed:
        return "diagnose"
    if not journey.has_generated_path:
        return "generate-path"
    # 「全部完成 → review」以 6 已验证基准点为准（体系扩到 78 点不改旅程推导）
    from app.services import knowledge_catalog

    core_ids = knowledge_catalog.core_kp_ids(db)
    if core_ids and all(status_map.get(kid) == STATUS_PASSED for kid in core_ids):
        return "review"
    return "learn"
