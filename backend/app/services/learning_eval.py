"""学习过程评估 · 行为数据层（C-fix 批3）。

复用既有真实数据汇总「学习行为信号」，并据此派生确定性多维指标——供
`agents.evaluation_agent` 在其上叠加 LLM 叙述（方法建议 / 动态调整）。

信号来源（全部复用既有表，缺口仅靠 QuizAttempt 轻量埋点补齐）：
- 做题表现：`QuizAttempt`（9.1 提交埋点）——次数、均分、每 KP 最佳分、通过率、
  重做次数、分数趋势（按时间先后比较）。
- 掌握进度：`Mastery`（7.1 三态）——已通过/学习中/待检核计数、核心覆盖率。
- 学习投入：`LearningStepProgress`（18.3 六步）已完成步骤数 + 分步类型计数；
  `LearningNote`（18.4）非空笔记数。
- 旅程：`Journey`——是否已诊断 / 已生成路径 / 目标岗位。

**全部由真实数据派生、因人而异，禁止臆造**；新用户（无任何行为）各项归 0 / 中性。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import (
    Journey,
    LearningNote,
    LearningStepProgress,
    QuizAttempt,
)
from app.services import knowledge_catalog
from app.services import mastery as mastery_service

# 趋势判定阈值（后半程均分 - 前半程均分）
_TREND_DELTA = 5.0


def _trend(scores: list[int]) -> str:
    """分数趋势：按时间先后比较前/后半程均分。improving|declining|stable。"""
    if len(scores) < 2:
        return "stable"
    mid = len(scores) // 2
    early = scores[:mid] or scores[:1]
    late = scores[mid:]
    delta = sum(late) / len(late) - sum(early) / len(early)
    if delta > _TREND_DELTA:
        return "improving"
    if delta < -_TREND_DELTA:
        return "declining"
    return "stable"


def gather_signals(db: Session, user_id: str) -> dict[str, Any]:
    """汇总该用户的学习行为信号（全部真实数据派生）。"""
    # 核心覆盖/统计仍以 6 已验证基准点为口径（体系扩到 78 点不改变学习评估分母）
    kps = sorted(knowledge_catalog.core_kps(db), key=lambda k: k.lesson_seq)
    kp_name = {kp.id: kp.name for kp in kps}
    total_core = len(kps)

    status_map = mastery_service.get_status_map(db, user_id)
    passed = [k for k, v in status_map.items() if v == mastery_service.STATUS_PASSED]
    pending = [k for k, v in status_map.items() if v == mastery_service.STATUS_PENDING_CHECK]
    learning = [k for k, v in status_map.items() if v == mastery_service.STATUS_LEARNING]

    attempts = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.user_id == user_id)
        .order_by(QuizAttempt.created_at, QuizAttempt.id)
        .all()
    )
    scores = [int(a.score) for a in attempts]
    best_by_kp: dict[str, int] = {}
    attempts_by_kp: dict[str, int] = {}
    for a in attempts:
        best_by_kp[a.kp_id] = max(best_by_kp.get(a.kp_id, 0), int(a.score))
        attempts_by_kp[a.kp_id] = attempts_by_kp.get(a.kp_id, 0) + 1
    attempt_count = len(attempts)
    pass_attempts = sum(1 for a in attempts if a.passed)
    avg_score = round(sum(scores) / len(scores)) if scores else 0
    avg_best = round(sum(best_by_kp.values()) / len(best_by_kp)) if best_by_kp else 0
    # 重做次数 = 总作答数 - 作答过的 KP 数（>0 表示有重做）
    retries = attempt_count - len(attempts_by_kp)
    # 平均通过尝试次数（仅统计已通过的 KP，越少越高效）
    attempts_to_pass = [attempts_by_kp[k] for k in passed if k in attempts_by_kp]
    avg_attempts_to_pass = (
        round(sum(attempts_to_pass) / len(attempts_to_pass), 2)
        if attempts_to_pass
        else None
    )

    steps = (
        db.query(LearningStepProgress)
        .filter(LearningStepProgress.user_id == user_id, LearningStepProgress.done.is_(True))
        .all()
    )
    steps_done = len(steps)
    steps_by_type: dict[str, int] = {}
    for s in steps:
        steps_by_type[s.step] = steps_by_type.get(s.step, 0) + 1

    notes = (
        db.query(LearningNote)
        .filter(LearningNote.user_id == user_id)
        .all()
    )
    notes_filled = sum(
        1 for n in notes if (n.main_notes or "").strip() or (n.summary or "").strip()
    )

    journey = db.get(Journey, user_id)

    return {
        "totalCore": total_core,
        "masteredCount": len(passed),
        "pendingCount": len(pending),
        "learningCount": len(learning),
        "masteredKpIds": passed,
        "kpNames": kp_name,
        "kpOrder": [kp.id for kp in kps],
        "attemptCount": attempt_count,
        "passAttempts": pass_attempts,
        "avgScore": avg_score,
        "avgBestScore": avg_best,
        "bestByKp": best_by_kp,
        "attemptsByKp": attempts_by_kp,
        "retries": retries,
        "avgAttemptsToPass": avg_attempts_to_pass,
        "scoreTrend": _trend(scores),
        "lastScore": scores[-1] if scores else None,
        "stepsDone": steps_done,
        "stepsByType": steps_by_type,
        "notesFilled": notes_filled,
        "diagnosed": bool(journey and journey.has_diagnosed),
        "pathGenerated": bool(journey and journey.has_generated_path),
        "targetJobName": journey.target_job_name if journey else None,
    }


def _clamp(x: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, round(x)))


def compute_metrics(signals: dict[str, Any]) -> dict[str, Any]:
    """由行为信号派生确定性多维指标（0-100）+ 综合分 + 趋势 + 薄弱点。

    维度：掌握进度 / 测验表现 / 学习效率 / 学习投入。综合分为四维加权均值。
    """
    total_core = signals["totalCore"] or 1

    mastery_progress = _clamp(signals["masteredCount"] / total_core * 100)
    quiz_performance = _clamp(signals["avgBestScore"]) if signals["attemptCount"] else 0

    # 学习效率：通过所需平均尝试次数越少越高 + 趋势微调；无数据中性 50
    if signals["avgAttemptsToPass"] is not None:
        efficiency = 100 - (signals["avgAttemptsToPass"] - 1) * 30
    elif signals["attemptCount"]:
        efficiency = 55  # 有作答但尚无通过：偏中性略低
    else:
        efficiency = 50
    if signals["scoreTrend"] == "improving":
        efficiency += 12
    elif signals["scoreTrend"] == "declining":
        efficiency -= 12
    efficiency = _clamp(efficiency)

    # 学习投入：步骤完成 + 笔记 + 作答活跃度（封顶 100）
    engagement = _clamp(
        signals["stepsDone"] * 6 + signals["notesFilled"] * 8 + signals["attemptCount"] * 4
    )

    dimensions = [
        {"key": "mastery_progress", "label": "掌握进度", "score": mastery_progress,
         "detail": f"已通过 {signals['masteredCount']}/{signals['totalCore']} 个核心知识点"},
        {"key": "quiz_performance", "label": "测验表现", "score": quiz_performance,
         "detail": (f"近 {signals['attemptCount']} 次作答，平均最佳 {signals['avgBestScore']} 分"
                    if signals["attemptCount"] else "暂无测验记录")},
        {"key": "learning_efficiency", "label": "学习效率", "score": efficiency,
         "detail": (f"平均 {signals['avgAttemptsToPass']} 次通过 · 趋势{_TREND_LABEL[signals['scoreTrend']]}"
                    if signals["avgAttemptsToPass"] is not None
                    else f"趋势{_TREND_LABEL[signals['scoreTrend']]}")},
        {"key": "engagement", "label": "学习投入", "score": engagement,
         "detail": f"完成 {signals['stepsDone']} 个学习步骤 · {signals['notesFilled']} 份笔记"},
    ]

    overall = _clamp(
        mastery_progress * 0.35
        + quiz_performance * 0.30
        + efficiency * 0.20
        + engagement * 0.15
    )

    # 薄弱点：未通过的核心知识点（有测验记录的标注最佳分），按 lesson_seq 取前 3
    weak_points: list[dict[str, Any]] = []
    for kp_id in signals["kpOrder"]:
        if kp_id in signals["masteredKpIds"]:
            continue
        best = signals["bestByKp"].get(kp_id)
        reason = (
            f"测验最高 {best} 分，尚未通过" if best is not None else "尚未开始测验"
        )
        weak_points.append({"kpId": kp_id, "name": signals["kpNames"].get(kp_id, kp_id), "reason": reason})
    weak_points = weak_points[:3]

    return {
        "overallScore": overall,
        "level": _level(overall),
        "trend": signals["scoreTrend"],
        "dimensions": dimensions,
        "weakPoints": weak_points,
    }


_TREND_LABEL = {"improving": "上升", "declining": "下滑", "stable": "平稳"}


def _level(score: int) -> str:
    if score >= 80:
        return "表现优异"
    if score >= 60:
        return "稳步推进"
    if score >= 40:
        return "起步阶段"
    return "刚刚起步"
