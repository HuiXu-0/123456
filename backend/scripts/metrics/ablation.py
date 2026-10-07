# -*- coding: utf-8 -*-
"""B11 消融实验：对工作流的三个组件做开关消融，对照幻觉率。

配置（实验开关仅经 run_learning_workflow 的实验参数注入，不暴露给任何业务接口/配置）：
  - 完整链路：RAG 检索 + critic 审核 + 重试降级（业务默认）
  - −RAG    ：关检索（generation 无上下文），critic/重试保留
  - −critic ：关审核闭环（首版直接交付，无重试/降级）
  - −重试   ：max_retries=0（低分即降级，不回炉）

每个配置对 6 AI 知识点 × 3 难度档各生成一份讲义（不走缓存，逐份新生成），
幻觉率口径与 B8 完全一致：逐句对「真实检索切片」做 embedding 接地
（sentence_grounding，阈值 settings.grounding_threshold）——所有配置统一以真实
RAG 上下文为接地基准（分母一致），从而公平比较「模型实际生成 vs 应有知识」的偏离。

运行（backend 目录，deepseek 真实生成）：
    python scripts/metrics/ablation.py --out scripts/metrics/_out/ablation.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.entities import KnowledgePoint, User  # noqa: E402
from app.rag.grounding import sentence_grounding  # noqa: E402
from app.services.resource import _retrieve_for_lecture  # noqa: E402
from app.workflows.learning_workflow import run_learning_workflow  # noqa: E402

AI_KPS = ["ml", "nn", "dl", "cnn", "transformer", "finetune"]
DIFFICULTIES = ["入门", "初级", "高级"]


def _empty_retriever(kp_id: str, kp_name: str, difficulty: str):
    return []


# 配置 → run_learning_workflow 实验参数
CONFIGS = {
    "full":     dict(retriever=_retrieve_for_lecture, disable_critic=False, max_retries=2),
    "-RAG":     dict(retriever=_empty_retriever,      disable_critic=False, max_retries=2),
    "-critic":  dict(retriever=_retrieve_for_lecture, disable_critic=True,  max_retries=2),
    "-retry":   dict(retriever=_retrieve_for_lecture, disable_critic=False, max_retries=0),
}


def _generate(db, user_id: str, kp_id: str, kp_name: str, difficulty: str, cfg: dict) -> dict:
    """按指定配置新生成一份讲义并对真实检索上下文测幻觉率。"""
    # R4.5 消融隔离：**显式关闭 Tool Loop，不依赖 .env / 环境默认值**。
    # 目的：保证 B11 消融测的仍是原实验变量（RAG / critic / 重试），
    # 不混入 R4.4 引入的 Tool Loop 取证步骤。
    # 只动这一个开关 —— 算法 / 指标 / 输出格式一律不变。
    settings.agent_tool_loop_enabled = False

    result = run_learning_workflow(
        db, user_id=user_id, kp_id=kp_id, difficulty=difficulty,
        retriever=cfg["retriever"], disable_critic=cfg["disable_critic"],
        max_retries=cfg["max_retries"],
    )
    final = result["finalOutput"]
    markdown = final["markdown"]
    contexts = [c["content"] for c in _retrieve_for_lecture(kp_id, kp_name, difficulty)]
    g = sentence_grounding(markdown, contexts)
    return {
        "kpId": kp_id, "difficulty": difficulty,
        "hallucinationRate": g["hallucinationRate"],
        "totalSentences": g["totalSentences"],
        "degraded": bool(final.get("degraded")),
        "retryCount": final.get("retryCount", 0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    measured_at = datetime.now(timezone.utc).isoformat()

    # R4.5：入口处先显式关掉 Tool Loop（真正在 _generate 内再兜一次），
    # 并把这一事实打印到 stdout —— **不进 JSON 输出**，故不改实验输出格式。
    if settings.agent_tool_loop_enabled:
        print(
            "  [R4.5] 检测到 AGENT_TOOL_LOOP_ENABLED=true → 本次消融强制关闭 Tool Loop",
            flush=True,
        )
    settings.agent_tool_loop_enabled = False

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "learner_001").one()
        names = {kp.id: kp.name for kp in db.query(KnowledgePoint).all()}
        per_config: dict[str, list] = {}
        for cfg_name, cfg in CONFIGS.items():
            rows = []
            for kp_id in AI_KPS:
                for difficulty in DIFFICULTIES:
                    print(f"  [{cfg_name}] {kp_id} × {difficulty} …", flush=True)
                    rows.append(_generate(db, user.id, kp_id, names[kp_id], difficulty, cfg))
            per_config[cfg_name] = rows
    finally:
        db.close()

    summary = {}
    for cfg_name, rows in per_config.items():
        avg = round(sum(r["hallucinationRate"] for r in rows) / len(rows), 4)
        degraded = sum(1 for r in rows if r["degraded"])
        retried = sum(1 for r in rows if r["retryCount"] > 0)
        summary[cfg_name] = {
            "meanHallucinationRate": avg, "sampleSize": len(rows),
            "degradedCount": degraded, "retriedCount": retried,
        }

    payload = {
        "measuredAt": measured_at, "provider": settings.llm_provider,
        "threshold": settings.grounding_threshold,
        "difficulties": DIFFICULTIES, "kps": AI_KPS,
        "summary": summary, "perConfig": per_config,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ablation] 写入 {out}")
    for cfg_name, s in summary.items():
        print(f"  {cfg_name:<8} 平均幻觉率={s['meanHallucinationRate']:.4f} "
              f"降级={s['degradedCount']}/{s['sampleSize']} 触发重试={s['retriedCount']}")


if __name__ == "__main__":
    main()
