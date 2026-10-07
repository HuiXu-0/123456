# -*- coding: utf-8 -*-
"""B8 量化指标汇总：一次生成 18 份讲义，串跑三项指标，输出 docs/metrics-report.md。

运行（backend 目录）：python scripts/metrics/make_report.py
结束时打印三比率与计算时间戳——把它们同步到
app/api/v1/admin_metrics.py 的 _MEASURED_RATES / _MEASURED_AT（接口 14.6 真值）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import difficulty_adaptation  # noqa: E402
import hallucination_rate  # noqa: E402
import knowledge_coverage  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.entities import User  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parents[3] / "docs" / "metrics-report.md"
DIFFS = hallucination_rate.DIFFICULTIES


def _verdict(r: dict) -> str:
    op = "<" if r["metric"] == "hallucinationRate" else "≥"
    return (f"**{r['rate']:.4f}**（目标 {op} {r['target']}）→ "
            + ("✅ 达标" if r["meetsTarget"] else "❌ 未达标"))


def main() -> None:
    measured_at = datetime.now(timezone.utc).isoformat()
    print(f"[metrics] provider={settings.llm_provider}，生成/复用 18 份讲义 …")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "learner_001").one()
        lectures = hallucination_rate.collect_lectures(db, user.id)
    finally:
        db.close()

    print("[metrics] ① 幻觉率（逐句接地）…")
    h = hallucination_rate.run(lectures)
    print("[metrics] ② 难度适配率（三档区分度）…")
    a = difficulty_adaptation.run(lectures)
    print("[metrics] ③ 知识覆盖率（核心概念命中）…")
    c = knowledge_coverage.run(lectures)

    lines: list[str] = [
        "# 智学中枢 · B8 量化指标报告",
        "",
        f"- **计算时间：** {measured_at}",
        f"- **LLM Provider：** `{settings.llm_provider}`"
        "（讲义经 `POST /resource/lecture` 同源管线生成，缓存复用）",
        f"- **样本：** 6 核心知识点 × 3 难度档 = 18 份讲义",
        f"- **接地阈值：** {settings.grounding_threshold}（15.3 逐句接地口径）",
        "",
        "| 指标 | 口径 | 实测值 | 目标 | 结论 |",
        "|---|---|---|---|---|",
        f"| 幻觉率 hallucinationRate | 未接地句数/总句数，18 份均值 | {h['rate']:.4f} "
        f"| < {h['target']} | {'✅ 达标' if h['meetsTarget'] else '❌ 未达标'} |",
        f"| 难度适配率 adaptationRate | 三档难度分有序对正确率（{a['totalPairs']} 对） "
        f"| {a['rate']:.4f} | ≥ {a['target']} | {'✅ 达标' if a['meetsTarget'] else '❌ 未达标'} |",
        f"| 知识覆盖率 coverageRate | 核心概念命中/{c['totalConcepts']} 项清单 "
        f"| {c['rate']:.4f} | ≥ {c['target']} | {'✅ 达标' if c['meetsTarget'] else '❌ 未达标'} |",
        "",
        "## ① 幻觉率（逐句接地校验）",
        "",
        _verdict(h),
        "",
        "| 知识点 | 难度 | 句数 | 幻觉率 | 未接地句（示例） |",
        "|---|---|---:|---:|---|",
    ]
    for d in h["details"]:
        example = d["ungrounded"][0][:40].replace("|", "\\|") if d["ungrounded"] else "—"
        lines.append(f"| {d['kpId']} | {d['difficulty']} | {d['totalSentences']} "
                     f"| {d['rate']:.4f} | {example} |")

    lines += [
        "",
        "## ② 难度适配率（三档区分度）",
        "",
        _verdict(a),
        "",
        "难度分 = 2.0×代码块 + 3.0×数学符号 + 1.5×高级术语 − 2.0×入门话术 + 长度/2000；"
        "逐 KP 检查 入门<初级、初级<高级、入门<高级 三个有序对（严格小于）。",
        "",
        "| 知识点 | 入门 | 初级 | 高级 | 失序对 |",
        "|---|---:|---:|---:|---|",
    ]
    for d in a["details"]:
        bad = "、".join(p["pair"] for p in d["pairs"] if not p["ok"]) or "—"
        s = {diff: d["features"][diff]["score"] for diff in DIFFS}
        lines.append(f"| {d['kpId']} | {s['入门']} | {s['初级']} | {s['高级']} | {bad} |")

    lines += [
        "",
        "## ③ 知识覆盖率（核心概念清单命中）",
        "",
        _verdict(c),
        "",
        "| 知识点 | 命中 | 未命中概念 |",
        "|---|---|---|",
    ]
    for d in c["details"]:
        misses = "、".join(d["misses"]) or "—"
        lines.append(f"| {d['kpId']} | {len(d['hits'])}/{len(d['hits']) + len(d['misses'])} "
                     f"| {misses} |")

    lines += [
        "",
        "---",
        "",
        "## 接入指标看板（接口 14.6）",
        "",
        "`GET /api/v1/admin/metrics` 的三比率取自本报告（`app/api/v1/admin_metrics.py`"
        " `_MEASURED_RATES`，`updatedAt` 即本次计算时间戳）：",
        "",
        "```python",
        f'_MEASURED_AT = "{measured_at}"',
        "_MEASURED_RATES = {",
        f'    "hallucinationRate": {h["rate"]},',
        f'    "adaptationRate": {a["rate"]},',
        f'    "coverageRate": {c["rate"]},',
        "}",
        "```",
        "",
        "复现：`cd backend && python scripts/metrics/make_report.py`"
        "（mock 模式无 Key 可跑；deepseek 模式为真实生成口径）。",
        "",
    ]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[metrics] 报告已写入 {REPORT_PATH}")
    print(f"[metrics] measuredAt = {measured_at}")
    print(f"[metrics] hallucinationRate = {h['rate']}  ({'达标' if h['meetsTarget'] else '未达标'})")
    print(f"[metrics] adaptationRate    = {a['rate']}  ({'达标' if a['meetsTarget'] else '未达标'})")
    print(f"[metrics] coverageRate      = {c['rate']}  ({'达标' if c['meetsTarget'] else '未达标'})")


if __name__ == "__main__":
    main()
