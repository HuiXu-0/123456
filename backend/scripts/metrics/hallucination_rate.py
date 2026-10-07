# -*- coding: utf-8 -*-
"""B8 量化指标 ①：幻觉率（目标 <5%）。

口径（接口文档 15.3 / 需求文档 7.2.1）：对 6 核心知识点 × 3 难度档共 18 份
讲义（经 services.resource.generate_lecture 按当前 LLM_PROVIDER 生成，命中缓存
即复用——「同档不重复再生成」），逐句与该讲义的 RAG 检索命中切片做 embedding
相似度比对（rag.grounding.sentence_grounding，阈值 settings.grounding_threshold），
hallucinationRate = 未接地句数 / 总句数，取 18 份的算术平均。

运行（backend 目录）：
    python scripts/metrics/hallucination_rate.py
依赖 backend/.env 的 LLM_PROVIDER：deepseek = 真实生成（需 Key）；mock = 模板兜底。
本模块同时提供 collect_lectures() 供其余两个指标脚本复用同一批讲义。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# 脚本直跑引导：backend 根目录入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.entities import KnowledgePoint, User  # noqa: E402
from app.rag.grounding import sentence_grounding  # noqa: E402
from app.services.resource import (  # noqa: E402
    _retrieve_for_lecture,
    generate_lecture,
)

DIFFICULTIES = ["入门", "初级", "高级"]
TARGET = 0.05


def collect_lectures(db, user_id: str) -> dict[tuple[str, str], str]:
    """6 KP × 3 难度 → markdown（缓存命中直接复用，否则按当前 provider 生成）。"""
    lectures: dict[tuple[str, str], str] = {}
    kps = db.query(KnowledgePoint).order_by(KnowledgePoint.lesson_seq).all()
    for kp in kps:
        for difficulty in DIFFICULTIES:
            print(f"  [讲义] {kp.id} × {difficulty} …", flush=True)
            payload = generate_lecture(db, user_id, kp.id, difficulty)
            lectures[(kp.id, difficulty)] = payload["markdown"]
    return lectures


def run(lectures: dict[tuple[str, str], str] | None = None) -> dict[str, Any]:
    """计算 18 份讲义的逐句接地幻觉率。返回 {rate, meetsTarget, details, ...}。"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "learner_001").one()
        if lectures is None:
            lectures = collect_lectures(db, user.id)
        kp_names = {kp.id: kp.name for kp in db.query(KnowledgePoint).all()}

        details: list[dict[str, Any]] = []
        for (kp_id, difficulty), markdown in sorted(lectures.items()):
            contexts = [
                c["content"]
                for c in _retrieve_for_lecture(kp_id, kp_names[kp_id], difficulty)
            ]
            res = sentence_grounding(markdown, contexts)
            details.append(
                {
                    "kpId": kp_id,
                    "difficulty": difficulty,
                    "rate": res["hallucinationRate"],
                    "totalSentences": res["totalSentences"],
                    "ungrounded": res["ungroundedSentences"],
                }
            )
        avg = round(sum(d["rate"] for d in details) / len(details), 4)
        return {
            "metric": "hallucinationRate",
            "rate": avg,
            "target": TARGET,
            "meetsTarget": avg < TARGET,
            "provider": settings.llm_provider,
            "threshold": settings.grounding_threshold,
            "sampleSize": len(details),
            "details": details,
        }
    finally:
        db.close()


def main() -> None:
    result = run()
    print(f"\n== 幻觉率（逐句接地，阈值 {result['threshold']}，provider={result['provider']}）==")
    for d in result["details"]:
        flag = "" if d["rate"] < TARGET else "  ← 超标"
        print(f"  {d['kpId']:<12} {d['difficulty']}  句数 {d['totalSentences']:>3}  "
              f"幻觉率 {d['rate']:.4f}{flag}")
        for s in d["ungrounded"][:2]:
            print(f"      未接地: {s[:60]}")
    verdict = "达标 ✓" if result["meetsTarget"] else "未达标 ✗"
    print(f"\n  平均幻觉率 = {result['rate']:.4f}（目标 < {TARGET}）→ {verdict}")


if __name__ == "__main__":
    main()
