"""诊断微测引擎（C2 重构：能力靠测不靠说）。

复用现有 quiz 题库（QuizQuestion / services.quiz 判分口径）作微测来源，按**知识点
难度阶梯**（Lesson.difficulty：ml 入门 → finetune 精通，借 KnowledgePoint.lesson_seq
升序）由浅入深逐知识点取 1 道客观题，用行为反推真实能力，而非开放式自陈：

- 答对 → 该知识点能力基线偏高；答错 → 偏低；**空作答 → 未测/低置信（不臆造高分）**；
- 产出能力维度 `knowledge_base`（0-100 + 依据 basis，可解释、防臆造）；
- 把各知识点基线写入 **Mastery（低置信 source=diagnostic）**，与后续 quiz「同一行、
  同一口径」覆盖更新——能力来源唯一，绝不两个互相打架（C2 决策）。

注意：QuizQuestion 无逐题难度字段，难度只存在于知识点/课程级（Lesson.difficulty），
故"由浅入深"取知识点级阶梯（lesson_seq 升序），非逐题难度。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import _PORTRAIT_LABELS
from app.models.entities import QuizQuestion
from app.services import knowledge_catalog
from app.services import mastery as mastery_service
from app.services.quiz import _is_correct, _list_questions

# 微测取题的客观题型（简答/多选不入微测：判分歧义、不利"速测由浅入深"）
_MICROTEST_TYPES: tuple[str, ...] = ("single", "boolean")

# 诊断基线置信度（低）：单题速测仅为"初始基线"，后续真实 quiz 高置信覆盖（口径统一）
_BASELINE_CONFIDENCE: float = 0.45
# 答对/答错时该知识点的能力基线分（行为反推，非自陈）
_SCORE_CORRECT: int = 78
_SCORE_WRONG: int = 32


def select_microtest(db: Session) -> list[dict[str, Any]]:
    """按知识点难度阶梯（lesson_seq 升序 = 入门→精通）逐知识点取 1 道客观题。

    返回 [{questionId, kpId, kpName, questionText, options, type}]，由浅入深排列，
    供编排层逐题"有选择性地抛出"（引导式微测）。题库缺该知识点客观题则跳过。
    """
    # 诊断微测**只对 6 已验证基准点**出题（复用现有题库，不为 78 点全出题）；其余 72
    # 目录点靠先修推断覆盖（见 knowledge_catalog.infer_prerequisite_closure）。
    kps = sorted(knowledge_catalog.core_kps(db), key=lambda k: k.lesson_seq)
    picked: list[dict[str, Any]] = []
    for kp in kps:
        question = _first_objective(db, kp.id)
        if question is None:
            continue
        picked.append(
            {
                "questionId": question.question_id,
                "kpId": kp.id,
                "kpName": kp.name,
                "questionText": question.question_text,
                "options": list(question.options or []),
                "type": question.question_type,
            }
        )
    return picked


def _first_objective(db: Session, kp_id: str) -> QuizQuestion | None:
    """该知识点首道客观速测题（single/boolean，按题号自然序取最浅一题）。"""
    for q in _list_questions(db, kp_id):
        if q.question_type in _MICROTEST_TYPES:
            return q
    return None


def grade_answer(db: Session, question_id: str, answer: Any) -> bool | None:
    """判定某微测题作答是否正确；空作答（None/空串）→ None（未测，不计入答对/答错）。"""
    if answer is None or (isinstance(answer, str) and not answer.strip()):
        return None
    question = db.get(QuizQuestion, question_id)
    if question is None:
        return None
    return _is_correct(question, answer)


def build_ability_dimension(results: list[dict[str, Any]]) -> dict[str, Any]:
    """据微测作答结果组装能力维度 knowledge_base（0-100 + 依据 basis）。

    results: [{kpId, kpName, correct: bool|None}]（correct=None 表示该题未作答）。
    - 至少一题作答 → score = 答对数/作答数×100，source=diagnostic，带依据 basis；
    - 全部未作答 → 不打分（value=未测、source=inferred、低置信），不臆造高分（验证1）。
    confidence 随作答题数上升（更多题 = 基线更可信），但封顶 0.7（仍是基线，待 quiz 精修）。
    """
    answered = [r for r in results if r["correct"] is not None]
    label = _PORTRAIT_LABELS["knowledge_base"]
    if not answered:
        return {
            "key": "knowledge_base",
            "label": label,
            "kind": "ability",
            "value": "未测（诊断微测未作答）",
            "confidence": 0.2,
            "source": "inferred",
            "basis": "诊断微测全部跳过，能力未测，不臆造分数；可重做诊断或在测验中补测。",
        }
    correct = [r for r in answered if r["correct"]]
    wrong = [r for r in answered if not r["correct"]]
    score = round(len(correct) / len(answered) * 100)
    value = _verdict(score)
    confidence = round(min(0.7, 0.4 + 0.06 * len(answered)), 2)
    basis = _compose_basis(len(correct), len(answered), correct, wrong)
    return {
        "key": "knowledge_base",
        "label": label,
        "kind": "ability",
        "value": value,
        "score": score,
        "confidence": confidence,
        "source": "diagnostic",
        "basis": basis,
    }


def _verdict(score: int) -> str:
    if score >= 80:
        return "扎实"
    if score >= 50:
        return "一般"
    return "薄弱"


def _compose_basis(
    correct_n: int, total_n: int, correct: list[dict], wrong: list[dict]
) -> str:
    parts = [f"诊断微测答对 {correct_n}/{total_n} 题"]
    if correct:
        parts.append("答对：" + "、".join(r["kpName"] for r in correct))
    if wrong:
        parts.append("答错：" + "、".join(r["kpName"] for r in wrong))
    return "；".join(parts) + "。"


def write_baseline(db: Session, user_id: str, results: list[dict[str, Any]]) -> None:
    """把各知识点微测基线写入 Mastery（低置信 source=diagnostic）。

    与后续真实 quiz 同一行（uq user+kp）覆盖更新——能力来源唯一、口径统一（C2 决策）：
    - 答对 → 基线分偏高；答错 → 偏低；未作答 → 不写（保持"未测"，不臆造）；
    - 仅写分数/置信/来源，**不置 passed**（不虚增知识图谱覆盖率）；新行落 learning，
      已 passed 的不回退（真实 quiz 已通过的能力不被诊断基线覆盖）。
    """
    for r in results:
        if r["correct"] is None:
            continue  # 未作答 → 不写基线（不臆造）
        score = _SCORE_CORRECT if r["correct"] else _SCORE_WRONG
        mastery_service.set_baseline(
            db, user_id, r["kpId"], score=score, confidence=_BASELINE_CONFIDENCE
        )
