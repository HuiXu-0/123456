# -*- coding: utf-8 -*-
"""B11 指标 · 知识库规模化对比：测量「当前已索引知识库」下的三量化指标。

同一脚本在两种 KB 状态各跑一次，产出可对比的 JSON：
  1) 灌入 30+ 文档前（4 份基线，复用 B8 缓存讲义）；
  2) seed_kb.py 灌入 30+ 文档并清空 AI 讲义缓存后（重生成 18 份）。

口径完全沿用 B8（hallucination_rate / difficulty_adaptation / knowledge_coverage
三模块的 run()），不改任何指标定义；仅记录当前 KB 文档数/切片数以支撑「规模化」对比。

运行（backend 目录）：
    python scripts/metrics/scale_compare.py --label 4docs  --out scripts/metrics/_out/scale_4docs.json
    python scripts/metrics/scale_compare.py --label 30docs --out scripts/metrics/_out/scale_30docs.json --purge
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

import difficulty_adaptation  # noqa: E402
import hallucination_rate  # noqa: E402
import knowledge_coverage  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.llm import get_llm  # noqa: E402
from app.models.entities import KnowledgeDocument, ResourceCache, User  # noqa: E402
from app.services import kb as kb_service  # noqa: E402

AI_KPS = ["ml", "nn", "dl", "cnn", "transformer", "finetune"]


def _purge_ai_lecture_cache(db) -> int:
    """清空 6 AI 知识点的讲义缓存（当前 provider 对应 kind），强制按新 KB 重生成。"""
    llm = get_llm()
    kind = "lecture" if llm.is_mock else f"lecture@{llm.provider}"
    n = (
        db.query(ResourceCache)
        .filter(ResourceCache.kp_id.in_(AI_KPS), ResourceCache.kind == kind)
        .delete(synchronize_session=False)
    )
    db.commit()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--purge", action="store_true", help="先清 AI 讲义缓存再重生成")
    args = ap.parse_args()

    measured_at = datetime.now(timezone.utc).isoformat()
    db = SessionLocal()
    try:
        if args.purge:
            removed = _purge_ai_lecture_cache(db)
            print(f"[scale] 已清 AI 讲义缓存 {removed} 行，将按当前 KB 重生成")
        doc_count = db.query(KnowledgeDocument).count()
        try:
            chunk_count = kb_service.get_vector_store().count()
        except Exception:  # noqa: BLE001
            chunk_count = -1
        user = db.query(User).filter(User.username == "learner_001").one()
        print(f"[scale] label={args.label} provider={settings.llm_provider} "
              f"docs={doc_count} chunks={chunk_count}：生成/复用 18 份讲义 …")
        lectures = hallucination_rate.collect_lectures(db, user.id)
    finally:
        db.close()

    h = hallucination_rate.run(lectures)
    a = difficulty_adaptation.run(lectures)
    c = knowledge_coverage.run(lectures)

    payload = {
        "label": args.label,
        "measuredAt": measured_at,
        "provider": settings.llm_provider,
        "threshold": settings.grounding_threshold,
        "docCount": doc_count,
        "chunkCount": chunk_count,
        "sampleSize": h["sampleSize"],
        "rates": {
            "hallucinationRate": h["rate"],
            "adaptationRate": a["rate"],
            "coverageRate": c["rate"],
        },
        "hallucinationDetails": h["details"],
        "adaptationDetails": [
            {"kpId": d["kpId"], "scores": {k: d["features"][k]["score"] for k in difficulty_adaptation.DIFFICULTIES},
             "badPairs": [p["pair"] for p in d["pairs"] if not p["ok"]]}
            for d in a["details"]
        ],
        "coverageDetails": [
            {"kpId": d["kpId"], "hit": len(d["hits"]), "total": len(d["hits"]) + len(d["misses"]),
             "misses": d["misses"]}
            for d in c["details"]
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[scale] 写入 {out}")
    print(f"[scale] {args.label}: 幻觉率={h['rate']} 适配率={a['rate']} 覆盖率={c['rate']} "
          f"(docs={doc_count}, chunks={chunk_count})")


if __name__ == "__main__":
    main()
