"""测验服务（B2-b；B6 追加 9.2 错题强化）。

覆盖接口文档 9.1 / 9.2：
- get_questions：GET /quiz/{kpId} → { questions: QuizQuestion[] }（种子题，含
  correct_answer/explanation，契约 2.5 要求一并返回）。
- submit：POST /quiz/{kpId}/submit → 判分；score≥70 → passed，并联动掌握度
  置 passed（7.3），返回 wrong[] 与 masteryUpdated。
- reinforce：POST /reinforce → 错题 → 薄弱点定位 → recap + 针对性练习
  （LLMClient 双模式：mock 确定性 / deepseek 真实生成；critic 审核练习题
  答案自洽后才返回，mock 产物同样过审保证口径统一）。

判分口径与前端 QuizRenderer 一致：single/boolean 直接比较，multiple 需集合相等
（顺序无关）。score = 答对数 / 总题数 × 100（四舍五入），passed = score ≥ 70。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import (
    SHORT_ANSWER_TYPE,
    LLMGenerationError,
    audit_practice,
    get_llm,
)
from app.models.entities import KnowledgePoint, QuizAttempt, QuizQuestion
from app.schemas.resource import QuizAnswerItem
from app.services import mastery as mastery_service
from app.services.learning_evidence import capture_shadow_event, from_quiz_result

PASS_SCORE = 70


class UnknownKnowledgePoint(Exception):
    """知识点不存在（→ code 1004 / 404）。"""


def _question_to_dict(q: QuizQuestion) -> dict[str, Any]:
    """转 QuizQuestion 契约结构（接口文档 2.5）。"""
    return {
        "question_id": q.question_id,
        "question_type": q.question_type,
        "question_text": q.question_text,
        "options": q.options,
        "correct_answer": q.correct_answer,
        "explanation": q.explanation,
    }


def _list_questions(db: Session, kp_id: str) -> list[QuizQuestion]:
    """题目列表（接口文档 9.1）。简答题恒置末尾，客观题按题号自然序（q1<…<q9<q10）。

    种子 question_id 形如 {kp}_q{n}，纯字符串排序会把 q10 排到 q2 前；按
    (是否简答, id 长度, id) 排序既保证简答收尾，又让客观题按题号递增。
    """
    rows = (
        db.query(QuizQuestion)
        .filter(QuizQuestion.kp_id == kp_id)
        .all()
    )
    return sorted(
        rows,
        key=lambda q: (
            q.question_type == SHORT_ANSWER_TYPE,
            len(q.question_id),
            q.question_id,
        ),
    )


def get_questions(db: Session, kp_id: str) -> dict[str, Any]:
    """获取测验题（接口文档 9.1）。知识点不存在 → 抛 UnknownKnowledgePoint。"""
    if db.get(KnowledgePoint, kp_id) is None:
        raise UnknownKnowledgePoint(kp_id)
    questions = _list_questions(db, kp_id)
    return {"questions": [_question_to_dict(q) for q in questions]}


def _is_correct(question: QuizQuestion, answer: Any) -> bool:
    """判定单题是否答对。multiple 需集合相等（顺序无关）。"""
    correct = question.correct_answer
    if isinstance(correct, list):
        if not isinstance(answer, list):
            return False
        return sorted(answer) == sorted(correct)
    return answer == correct


def submit(
    db: Session, user_id: str, kp_id: str, answers: list[QuizAnswerItem]
) -> dict[str, Any]:
    """提交作答并判分（接口文档 9.1，C-fix 批2 含简答 AI 评分）。

    综合得分口径：客观题（single/multiple/boolean）每题等权，答对计 1；简答题
    （short_answer）经 LLMClient.score_short_answer 给 0-100 分后按等权一题折算
    （score/100）。综合分 = (客观答对数 + Σ简答分/100) / 总题数 × 100（四舍五入）。
    综合 ≥ 70 → passed，并联动掌握度置 passed（7.3）。简答评分明细随回包返回
    （additive：shortAnswers[]），前端展示 AI 评分/点评。
    """
    if db.get(KnowledgePoint, kp_id) is None:
        raise UnknownKnowledgePoint(kp_id)

    questions = _list_questions(db, kp_id)
    total = len(questions)
    answer_map = {a.question_id: a.answer for a in answers}

    wrong: list[dict[str, Any]] = []
    short_answers: list[dict[str, Any]] = []
    objective_correct = 0
    short_score_sum = 0.0
    llm = get_llm()
    for q in questions:
        if q.question_type == SHORT_ANSWER_TYPE:
            student = answer_map.get(q.question_id)
            student_text = student if isinstance(student, str) else ""
            points = q.correct_answer if isinstance(q.correct_answer, list) else []
            graded = llm.score_short_answer(
                q.question_text, points, q.explanation or "", student_text
            )
            short_score_sum += graded["score"]
            short_answers.append(
                {
                    "questionId": q.question_id,
                    "score": graded["score"],
                    "comment": graded["comment"],
                }
            )
        elif _is_correct(q, answer_map.get(q.question_id)):
            objective_correct += 1
        else:
            wrong.append(_question_to_dict(q))

    if total:
        combined_units = objective_correct + short_score_sum / 100.0
        score = round(combined_units / total * 100)
    else:
        score = 0
    passed = score >= PASS_SCORE

    # C2 能力口径统一：真实 quiz 分写入 Mastery 能力分（高置信 source=quiz），覆盖
    # 诊断微测低置信基线同一行——4.4 能力雷达随真实测验精修（不与诊断基线打架）。
    mastery_service.set_score(db, user_id, kp_id, score=score, confidence=0.85)

    mastery_updated: dict[str, Any] | None = None
    if passed:
        status = mastery_service.mark_pass(db, user_id, kp_id)
        mastery_updated = {"id": kp_id, "status": status}

    # 行为数据埋点（C-fix 批3）：落一行作答历史，供学习评估 Agent 聚合（解耦判分）
    attempt = QuizAttempt(
        user_id=user_id,
        kp_id=kp_id,
        score=score,
        correct_count=objective_correct,
        total=total,
        passed=passed,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    capture_shadow_event(from_quiz_result(attempt))

    return {
        "score": score,
        "passed": passed,
        "correctCount": objective_correct,
        "total": total,
        "wrong": wrong,
        "shortAnswers": short_answers,
        "masteryUpdated": mastery_updated,
    }


def reinforce(
    db: Session, user_id: str, kp_id: str, wrong_question_ids: list[str]
) -> list[dict[str, Any]]:
    """错题强化生成（接口文档 9.2）。

    错题 id 经种子题库回查题面（未知 id 跳过）→ LLMClient.generate_reinforcement
    定位薄弱点并产出 recap + practice → critic 审核（audit_practice）兜底校验
    答案自洽（deepseek 分支内部已审核重试，此处为最终防线；mock 产物零成本同审）。
    """
    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise UnknownKnowledgePoint(kp_id)

    questions = {q.question_id: q for q in _list_questions(db, kp_id)}
    wrong = [
        _question_to_dict(questions[qid])
        for qid in wrong_question_ids
        if qid in questions
    ]
    if not wrong:
        return []

    cards = get_llm().generate_reinforcement(kp.name, wrong)
    for card in cards:
        issues = audit_practice(card["practice"])
        if issues:
            raise LLMGenerationError(f"强化练习审核未通过：{issues}")
    return cards
