# -*- coding: utf-8 -*-
"""B11 人工盲评统计：汇总多名评审的打分 CSV，输出逐维度/逐份/逐难度均分与一致性。

输入：--dir 指向存放各评审 CSV 的目录（表头见 docs/人工盲评打分表.md 第四节）；
可选 mapping.csv（列 lecture_id,difficulty）以启用逐难度档统计。
输出：--out JSON（逐维度均分±std、总均分、逐份均分、逐难度均分、评审一致性）。

运行（backend 目录）：
    python scripts/metrics/blind_eval_stats.py --dir scripts/metrics/_blind --out scripts/metrics/_out/blind_eval.json
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIMS = ["accuracy", "completeness", "difficulty_fit", "readability", "usefulness"]


def _mean_std(xs: list[float]) -> dict:
    return {"mean": round(st.mean(xs), 3) if xs else 0.0,
            "std": round(st.pstdev(xs), 3) if len(xs) > 1 else 0.0,
            "n": len(xs)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    src = Path(args.dir)
    rows: list[dict] = []
    mapping: dict[str, str] = {}
    if src.is_dir():
        for f in sorted(src.glob("*.csv")):
            if f.name == "mapping.csv":
                for m in csv.DictReader(f.open(encoding="utf-8-sig")):
                    if m.get("lecture_id"):
                        mapping[m["lecture_id"]] = m.get("difficulty", "")
                continue
            for row in csv.DictReader(f.open(encoding="utf-8-sig")):
                try:
                    rec = {d: float(row[d]) for d in DIMS}
                except (KeyError, ValueError):
                    continue
                rec["rater"] = row.get("rater", f.stem)
                rec["lecture_id"] = row.get("lecture_id", "")
                rows.append(rec)

    if not rows:
        print("[blind] 未读到任何评分行（请按模板放入 CSV）。仅写空结果。")
        payload = {"sampleRows": 0, "note": "暂无盲评数据；模板见 docs/人工盲评打分表.md"}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    # 逐维度均分
    by_dim = {d: _mean_std([r[d] for r in rows]) for d in DIMS}
    overall = _mean_std([st.mean([r[d] for d in DIMS]) for r in rows])

    # 逐份讲义均分 + 一致性（同份不同评审的离散度）
    by_lecture: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_lecture[r["lecture_id"]].append(st.mean([r[d] for d in DIMS]))
    lecture_stats = {lid: _mean_std(v) for lid, v in by_lecture.items()}
    within1 = [1 if (max(v) - min(v)) <= 1 else 0 for v in by_lecture.values() if len(v) > 1]
    agreement = round(sum(within1) / len(within1), 3) if within1 else None
    avg_item_std = round(st.mean([s["std"] for s in lecture_stats.values()]), 3) if lecture_stats else 0.0

    # 逐难度档均分（需 mapping.csv）
    by_diff: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        diff = mapping.get(r["lecture_id"])
        if diff:
            by_diff[diff].append(st.mean([r[d] for d in DIMS]))
    diff_stats = {d: _mean_std(v) for d, v in by_diff.items()}

    payload = {
        "sampleRows": len(rows),
        "raters": sorted({r["rater"] for r in rows}),
        "lectures": sorted(by_lecture),
        "byDimension": by_dim,
        "overall": overall,
        "byLecture": lecture_stats,
        "byDifficulty": diff_stats,
        "agreementWithin1": agreement,
        "avgItemStd": avg_item_std,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[blind] 写入 {args.out}")
    print(f"[blind] 评审 {len(payload['raters'])} 人，讲义 {len(by_lecture)} 份，评分行 {len(rows)}")
    print(f"[blind] 总均分 {overall['mean']}±{overall['std']}；维度均分：" +
          "  ".join(f"{d}={by_dim[d]['mean']}" for d in DIMS))
    print(f"[blind] 一致性：组内差≤1 占比 {agreement}，逐份均 std {avg_item_std}")


if __name__ == "__main__":
    main()
