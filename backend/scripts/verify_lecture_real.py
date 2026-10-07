# -*- coding: utf-8 -*-
"""现状核验脚本：/resource/lecture 真实管线（RAG → 生成 Agent → 审核 Agent）。

不改任何业务代码，仅调用 services.resource.generate_lecture 实测：
  1. 真实模式 sources 来自真实检索（对比 llm._LECTURE_SOURCES 占位常量）；
  2. 真实模式 hallucinationRate 为 15.3 逐句接地实算值（非固定 0.021）；
  3. 入门/初级/高级三档深度差异；
  4. mock 模式（不依赖 Key）仍可跑通。
为拿到「今天的实测值」，对所选 KP 先清 lecture@<provider> 缓存强制现生成。
运行（backend 目录）：python scripts/verify_lecture_real.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core import llm as llm_module  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.llm import LLMClient, _LECTURE_HALLUCINATION_RATE, _LECTURE_SOURCES  # noqa: E402
from app.models.entities import KnowledgePoint, ResourceCache, User  # noqa: E402
from app.services.resource import generate_lecture  # noqa: E402

KP_ID = "nn"
DIFFICULTIES = ["入门", "初级", "高级"]
PLACEHOLDER_TITLES = {s["title"] for s in _LECTURE_SOURCES}


def _clear_cache(db, kp_id: str, kind: str) -> int:
    rows = (
        db.query(ResourceCache)
        .filter(ResourceCache.kp_id == kp_id, ResourceCache.kind == kind)
        .all()
    )
    n = len(rows)
    for r in rows:
        db.delete(r)
    db.commit()
    return n


def _set_provider(provider: str) -> None:
    """切换进程内 LLM 单例（仅本核验脚本用，不改 .env）。"""
    llm_module._client = LLMClient(provider)


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "learner_001").one()
        kp = db.get(KnowledgePoint, KP_ID)
        print(f"配置 provider={settings.llm_provider} grounding_threshold={settings.grounding_threshold}")
        print(f"目标知识点：{kp.id} / {kp.name}\n")

        # ===== 真实模式（deepseek）=====
        print("===== 真实模式（deepseek，现生成）=====")
        cleared = _clear_cache(db, KP_ID, f"lecture@{settings.llm_provider}")
        print(f"清理旧缓存 {cleared} 条 → 强制现生成\n")
        real = {}
        for d in DIFFICULTIES:
            payload = generate_lecture(db, user.id, KP_ID, d)
            real[d] = payload
            print(f"[{d}] 字符数={len(payload['markdown'])}  "
                  f"hallucinationRate={payload['hallucinationRate']}  "
                  f"workflowId={payload['workflowId']}")
            for s in payload["sources"]:
                placeholder = "占位常量!" if s["title"] in PLACEHOLDER_TITLES else "真实检索"
                print(f"     source[{placeholder}] {s['title']}  ({s['type']}, conf={s['confidence']})")
            print()

        # 断言：sources 非占位、rate 非固定常量
        all_titles = [s["title"] for p in real.values() for s in p["sources"]]
        sources_real = all_titles and not (set(all_titles) & PLACEHOLDER_TITLES)
        rates = [real[d]["hallucinationRate"] for d in DIFFICULTIES]
        rate_real = any(abs(r - _LECTURE_HALLUCINATION_RATE) > 1e-9 for r in rates)
        avg_rate = round(sum(rates) / len(rates), 4)
        print(f"  → sources 全部真实检索：{sources_real}")
        print(f"  → hallucinationRate 非固定常量({_LECTURE_HALLUCINATION_RATE})：{rate_real}")
        print(f"  → 三档实测幻觉率={rates}  平均={avg_rate}  达标(<0.05)={avg_rate < 0.05}")
        lens = {d: len(real[d]["markdown"]) for d in DIFFICULTIES}
        distinct_md = len({real[d]["markdown"] for d in DIFFICULTIES}) == 3
        print(f"  → 三档字符数={lens}  正文互不相同={distinct_md}\n")

        # ===== Mock 模式（不依赖 Key）=====
        print("===== Mock 模式（provider=mock，不依赖 Key）=====")
        _set_provider("mock")
        _clear_cache(db, KP_ID, "lecture")
        ok = True
        for d in DIFFICULTIES:
            payload = generate_lecture(db, user.id, KP_ID, d)
            print(f"[{d}] 字符数={len(payload['markdown'])}  "
                  f"hallucinationRate={payload['hallucinationRate']}  "
                  f"sources={len(payload['sources'])}条  workflowId={payload['workflowId']}")
            ok = ok and bool(payload["markdown"]) and payload["sources"]
        print(f"  → mock 三档均产出讲义：{ok}")
    finally:
        _set_provider(settings.llm_provider)
        db.close()


if __name__ == "__main__":
    main()
