# -*- coding: utf-8 -*-
"""B11 性能与成本统计：汇总历史 WorkflowTrace 的生成耗时分布与单份讲义 token/成本估算。

- 性能：对所有真实工作流轨迹（WorkflowTrace），统计端到端生成耗时（ended−started）
  的 P50/P90/最大，以及各节点（诊断/生成/审核）平均耗时（node_log.durationMs）。
- 成本：以 deepseek 讲义缓存（lecture@deepseek）的 markdown 字符数估算单份输出规模，
  按声明的 token 折算因子与 DeepSeek 单价估算单份讲义的 token 用量与成本（**估算口径，
  非计费账单**）。

运行（backend 目录）：python scripts/metrics/perf_cost.py --out scripts/metrics/_out/perf_cost.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.database import SessionLocal  # noqa: E402
from app.models.entities import ResourceCache, WorkflowTrace  # noqa: E402

# 估算参数（公开声明，便于复核）
CHARS_PER_TOKEN = 1.6          # 中文混排经验：约 1.6 字符/token（DeepSeek BPE 估算）
EST_GEN_INPUT_TOKENS = 2500    # 单次生成输入估算：模板 + 6 检索切片上下文
EST_DIAG_TOKENS = 600          # 诊断调用输入+输出估算
PRICE_IN_PER_M = 2.0           # DeepSeek deepseek-chat 输入（无缓存）¥/百万 token
PRICE_OUT_PER_M = 8.0          # DeepSeek deepseek-chat 输出 ¥/百万 token


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    measured_at = datetime.now(timezone.utc).isoformat()

    db = SessionLocal()
    try:
        traces = db.query(WorkflowTrace).all()
        total_traces = len(traces)
        gen_secs: list[float] = []
        node_ms: dict[str, list[float]] = {"diagnosis": [], "generation": [], "critic": [], "rag": []}
        retried = degraded = 0
        for t in traces:
            if t.started_at and t.ended_at:
                gen_secs.append((t.ended_at - t.started_at).total_seconds())
            for n in (t.node_log or []):
                agent = n.get("agent")
                if agent in node_ms and isinstance(n.get("durationMs"), (int, float)):
                    node_ms[agent].append(float(n["durationMs"]))
            if (t.retry_count or 0) > 0:
                retried += 1
            if t.degraded:
                degraded += 1

        # 单份讲义输出规模：deepseek 讲义缓存 markdown 字符数
        rows = db.query(ResourceCache).filter(ResourceCache.kind == "lecture@deepseek").all()
        out_chars = [len(r.payload.get("markdown", "")) for r in rows if r.payload]
    finally:
        db.close()

    avg_out_chars = round(sum(out_chars) / len(out_chars), 1) if out_chars else 0.0
    est_out_tokens = round(avg_out_chars / CHARS_PER_TOKEN)
    est_in_tokens = EST_GEN_INPUT_TOKENS + EST_DIAG_TOKENS
    cost_per_lecture = round(
        est_in_tokens / 1_000_000 * PRICE_IN_PER_M
        + est_out_tokens / 1_000_000 * PRICE_OUT_PER_M, 5)

    payload = {
        "measuredAt": measured_at,
        "performance": {
            "totalTraces": total_traces,
            "workflowSampleSize": len(gen_secs),
            "genTimeSecP50": _percentile(gen_secs, 0.5),
            "genTimeSecP90": _percentile(gen_secs, 0.9),
            "genTimeSecMax": round(max(gen_secs), 2) if gen_secs else 0.0,
            "nodeAvgMs": {k: round(sum(v) / len(v), 1) if v else 0.0 for k, v in node_ms.items()},
            "retriedTraces": retried,
            "degradedTraces": degraded,
        },
        "cost": {
            "lectureSampleSize": len(out_chars),
            "avgOutputChars": avg_out_chars,
            "estOutputTokens": est_out_tokens,
            "estInputTokens": est_in_tokens,
            "charsPerToken": CHARS_PER_TOKEN,
            "priceInPerMillion": PRICE_IN_PER_M,
            "priceOutPerMillion": PRICE_OUT_PER_M,
            "estCostPerLectureRMB": cost_per_lecture,
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[perf] 写入 {out}")
    print(f"[perf] 生成耗时 P50={payload['performance']['genTimeSecP50']}s "
          f"P90={payload['performance']['genTimeSecP90']}s max={payload['performance']['genTimeSecMax']}s "
          f"(n={payload['performance']['workflowSampleSize']})")
    print(f"[perf] 单份讲义：均 {avg_out_chars} 字符 ≈ {est_out_tokens} 输出 token，"
          f"估算成本 ¥{cost_per_lecture}/份")


if __name__ == "__main__":
    main()
