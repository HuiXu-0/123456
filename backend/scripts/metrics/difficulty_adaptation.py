# -*- coding: utf-8 -*-
"""B8 量化指标 ②：难度适配率（目标 ≥85%）。

口径（需求文档 7.2.2 思想的内容侧落地）：同一知识点的「入门 / 初级 / 高级」三档
讲义应呈现可量化的难度特征区分度。对每份讲义提取难度特征并合成难度分：

    难度分 = 2.0×代码块数 + 3.0×数学符号命中 + 1.5×高级术语命中
             − 2.0×入门话术命中 + 正文长度/2000

对每个 KP 检查三档的 3 个有序对（入门<初级、初级<高级、入门<高级，严格小于），
adaptationRate = 排序正确的有序对数 / 总有序对数（6 KP × 3 对 = 18 对）。

运行（backend 目录）：python scripts/metrics/difficulty_adaptation.py
讲义来源与脚本 ① 同一批（generate_lecture 缓存复用）。
"""
from __future__ import annotations

import re
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hallucination_rate import DIFFICULTIES, collect_lectures  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.entities import User  # noqa: E402

TARGET = 0.85

# 难度特征词典（领域通用，不针对某一 provider 的措辞定制）
_CODE_FENCE_RE = re.compile(r"```")
_MATH_RE = re.compile(r"\\\(|\\\[|\$\$|[∂∇θΣ∈≈≤≥]|\\partial|\\theta|\\frac")
_ADVANCED_TERMS = [
    "形式化", "参数化", "复杂度", "收敛", "数值稳定", "权衡", "工程",
    "梯度", "优化器", "正则", "推导", "证明", "时间复杂度", "显存",
]
_INTRO_TERMS = [
    "比喻", "直觉", "直白", "一句话", "不必", "先把握", "通俗",
    "想象", "打个比方", "生活中", "入门",
]


def difficulty_score(markdown: str) -> dict[str, Any]:
    """难度特征 → 标量难度分（特征值一并返回，供报告展示）。"""
    code = len(_CODE_FENCE_RE.findall(markdown)) // 2  # 围栏成对 → 代码块数
    math = len(_MATH_RE.findall(markdown))
    advanced = sum(markdown.count(t) for t in _ADVANCED_TERMS)
    intro = sum(markdown.count(t) for t in _INTRO_TERMS)
    length = len(markdown)
    score = 2.0 * code + 3.0 * math + 1.5 * advanced - 2.0 * intro + length / 2000
    return {
        "code": code, "math": math, "advanced": advanced,
        "intro": intro, "length": length, "score": round(score, 2),
    }


def run(lectures: dict[tuple[str, str], str] | None = None) -> dict[str, Any]:
    """逐 KP 评估三档讲义的有序对正确率。"""
    if lectures is None:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == "learner_001").one()
            lectures = collect_lectures(db, user.id)
        finally:
            db.close()

    rank = {d: i for i, d in enumerate(DIFFICULTIES)}  # 入门0 < 初级1 < 高级2
    kp_ids = sorted({kp for kp, _ in lectures})
    details: list[dict[str, Any]] = []
    correct = total = 0
    for kp_id in kp_ids:
        feats = {d: difficulty_score(lectures[(kp_id, d)]) for d in DIFFICULTIES}
        pairs = []
        for low, high in combinations(DIFFICULTIES, 2):
            lo, hi = (low, high) if rank[low] < rank[high] else (high, low)
            ok = feats[lo]["score"] < feats[hi]["score"]  # 严格小于才算区分
            pairs.append({"pair": f"{lo}<{hi}", "ok": ok})
            correct += int(ok)
            total += 1
        details.append({"kpId": kp_id, "features": feats, "pairs": pairs})

    rate = round(correct / total, 4) if total else 0.0
    return {
        "metric": "adaptationRate",
        "rate": rate,
        "target": TARGET,
        "meetsTarget": rate >= TARGET,
        "provider": settings.llm_provider,
        "correctPairs": correct,
        "totalPairs": total,
        "details": details,
    }


def main() -> None:
    result = run()
    print(f"\n== 难度适配率（三档区分度，provider={result['provider']}）==")
    for d in result["details"]:
        scores = "  ".join(
            f"{diff}={d['features'][diff]['score']}" for diff in DIFFICULTIES
        )
        bad = [p["pair"] for p in d["pairs"] if not p["ok"]]
        flag = f"  ← 失序 {bad}" if bad else ""
        print(f"  {d['kpId']:<12} {scores}{flag}")
    verdict = "达标 ✓" if result["meetsTarget"] else "未达标 ✗"
    print(f"\n  适配率 = {result['correctPairs']}/{result['totalPairs']} "
          f"= {result['rate']:.4f}（目标 ≥ {TARGET}）→ {verdict}")


if __name__ == "__main__":
    main()
