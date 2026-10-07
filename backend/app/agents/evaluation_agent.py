"""学习过程评估 Agent（接口文档 12.2，C-fix 批3）。

赛题「学习评估 Agent」：基于真实学习行为数据产出**多维学习评估 + 动态调整建议**。
编排三步（数据 → 指标 → 叙述），与既有 Agent（诊断/生成/审核/规划/费曼）并列：

1. `learning_eval.gather_signals`：复用既有 Mastery/Journey/QuizAttempt/Steps/Notes 汇总
   真实学习行为信号（因人而异，新用户归 0/中性）；
2. `learning_eval.compute_metrics`：据信号派生确定性多维指标（掌握进度/测验表现/学习效率/
   学习投入）+ 综合分 + 趋势 + 薄弱点；
3. `LLMClient.evaluate_learning`：在指标之上叠加学习综述 + 方法建议（mock 确定性 / deepseek
   真实），并给确定性动态调整建议（下一步学什么 / 难度调整）。

输出供 12.2 `GET /dashboard/evaluation` 直出，前端「学习评估」面板渲染。
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import get_llm
from app.services import learning_eval

# 叙述层缓存：user_id → (signals 指纹, narrative)。
# 信号/指标为确定性派生（毫秒级、每次实时计算）；仅 LLM 叙述（真实模式 ~2.5s/次）
# 按行为指纹缓存——学习行为不变则叙述不必重生成，行为一变指纹即失效自动重算。
# 进程内存缓存与轻量栈约定一致（TTL 会话同风格），重启即清、无需失效协议。
_NARRATIVE_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}


def _fingerprint(signals: dict[str, Any], provider: str) -> str:
    return provider + "|" + json.dumps(signals, sort_keys=True, ensure_ascii=False, default=str)


def evaluate(db: Session, user_id: str) -> dict[str, Any]:
    """产出该用户的多维学习评估（接口文档 12.2）。"""
    signals = learning_eval.gather_signals(db, user_id)
    metrics = learning_eval.compute_metrics(signals)
    llm = get_llm()
    fp = _fingerprint(signals, "mock" if llm.is_mock else llm.provider)
    cached = _NARRATIVE_CACHE.get(user_id)
    if cached is not None and cached[0] == fp:
        narrative = cached[1]
    else:
        narrative = llm.evaluate_learning(signals, metrics)
        _NARRATIVE_CACHE[user_id] = (fp, narrative)

    return {
        "overallScore": metrics["overallScore"],
        "level": metrics["level"],
        "trend": metrics["trend"],
        "dimensions": metrics["dimensions"],
        "weakPoints": metrics["weakPoints"],
        "summary": narrative["summary"],
        "suggestions": narrative["suggestions"],
        "adjustment": narrative["adjustment"],
        "generatedBy": "mock" if llm.is_mock else llm.provider,
        # 透明化原始行为信号（轻量子集，供前端可选展示/调试，不喧宾夺主）
        "signals": {
            "masteredCount": signals["masteredCount"],
            "totalCore": signals["totalCore"],
            "attemptCount": signals["attemptCount"],
            "avgBestScore": signals["avgBestScore"],
            "stepsDone": signals["stepsDone"],
            "notesFilled": signals["notesFilled"],
            "retries": signals["retries"],
        },
    }
