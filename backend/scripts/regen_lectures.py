"""讲义缓存清理 + 重生成自检（一次性运维脚本，不改任何接口签名）。

用法（在 backend/ 下，激活同一环境）：
  python scripts/regen_lectures.py inspect          # 只读：列出当前讲义/图解缓存
  python scripts/regen_lectures.py validate         # 单点验证：重生成 finetune·入门
  python scripts/regen_lectures.py clear            # 删除 lecture% / diagram% 缓存行
  python scripts/regen_lectures.py batch            # 清理后逐 (kp×难度) 重生成 + 自检
  python scripts/regen_lectures.py tier             # finetune/nn 的 advanced vs beginner 深度对比

仅操作 resource_cache 表（讲义文本 + 内嵌配图/图解都在 payload.markdown 里；独立
图解 Tab 缓存 kind=diagram*）。video* 不在本次清理范围（任务只点名讲义/配图/图解）。
"""
from __future__ import annotations

import io
import re
import sys
import time
from typing import Any

from sqlalchemy import or_

from app.core.database import SessionLocal
from app.core.llm import LECTURE_DIFFICULTIES, get_llm
from app.models.entities import KnowledgePoint, ResourceCache, StudentPortrait
from app.services import resource as resource_svc

DIFFS = ["入门", "初级", "高级"]
CORE_KPS = ["ml", "nn", "dl", "cnn", "transformer", "finetune"]
BASE_USER = "u_10001"  # 空画像 → tier=None → 基线难度档（kind=lecture@deepseek 无 tier 后缀）

DEGRADED_MARK = "内容安全提示"


def _img_kind(md: str) -> tuple[str, str]:
    """识别讲义内嵌配图来源。返回 (kind, url_or_note)。"""
    # markdown 图片：![alt](url)
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", md):
        url = m.group(1)
        if "wikimedia.org" in url or "wikipedia.org" in url:
            return "wikimedia", url
        if url.startswith("data:image/svg"):
            return "svg-placeholder", "(内联SVG占位)"
        if url.startswith("http"):
            return "other-http", url
    return "none", ""


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    md = payload.get("markdown") or ""
    img_kind, img_url = _img_kind(md)
    # 章节结构信号
    sect = lambda kw: bool(re.search(rf"##\s*[一二三四五六]?[、.]?\s*[^\n]*{kw}", md))
    return {
        "len": len(md),
        "degraded": DEGRADED_MARK in md,
        "h2_count": len(re.findall(r"^##\s", md, re.M)),
        "intro": sect("概念引入|是什么|引入"),
        "principle": sect("核心原理|原理"),
        "example": sect("例子|示例|举例"),
        "code": "```python" in md,
        "pitfalls": sect("误区|注意"),
        "summary": sect("小结|总结"),
        "katex": ("$$" in md) or bool(re.search(r"(?<!\$)\$[^$\n]+\$", md)),
        "mermaid": "```mermaid" in md,
        "img_kind": img_kind,
        "img_url": img_url,
        "halluc": payload.get("hallucinationRate"),
        "sources": len(payload.get("sources") or []),
        "workflowId": payload.get("workflowId"),
    }


def _fmt(a: dict[str, Any]) -> str:
    flags = "".join(
        k[0].upper() if a[k] else "-"
        for k in ["intro", "principle", "example", "code", "pitfalls", "summary"]
    )
    bad = "  <<DEGRADED>>" if a["degraded"] else ""
    return (
        f"len={a['len']:>6} h2={a['h2_count']:>2} struct[IPECPS]={flags} "
        f"katex={'Y' if a['katex'] else 'n'} mermaid={'Y' if a['mermaid'] else 'n'} "
        f"img={a['img_kind']:<15} halluc={a['halluc']} src={a['sources']}{bad}"
    )


def _delete_keys(db, kp_id: str, difficulty: str, kind_prefix: str) -> int:
    rows = (
        db.query(ResourceCache)
        .filter(
            ResourceCache.kp_id == kp_id,
            ResourceCache.difficulty == difficulty,
            ResourceCache.kind.like(f"{kind_prefix}%"),
        )
        .all()
    )
    n = len(rows)
    for r in rows:
        db.delete(r)
    db.commit()
    return n


def cmd_inspect(db) -> None:
    rows = (
        db.query(ResourceCache)
        .filter(or_(ResourceCache.kind.like("lecture%"), ResourceCache.kind.like("diagram%")))
        .order_by(ResourceCache.kp_id, ResourceCache.kind, ResourceCache.difficulty)
        .all()
    )
    print(f"现存 lecture%/diagram% 缓存行：{len(rows)}")
    for r in rows:
        md = (r.payload or {}).get("markdown", "")
        flag = "  <<DEGRADED>>" if DEGRADED_MARK in md else ""
        print(f"  {r.kp_id:12} {r.kind:24} {r.difficulty or '∅':6} payload_len={len(str(r.payload))}{flag}")


def cmd_clear(db) -> None:
    rows = (
        db.query(ResourceCache)
        .filter(or_(ResourceCache.kind.like("lecture%"), ResourceCache.kind.like("diagram%")))
        .all()
    )
    n = len(rows)
    for r in rows:
        db.delete(r)
    db.commit()
    print(f"已删除 lecture%/diagram% 缓存行：{n}（video* 保留，未在清理范围）")


def _regen_one(db, kp_id: str, difficulty: str, user_id: str) -> dict[str, Any]:
    llm = get_llm()
    base_kind = "lecture" if llm.is_mock else f"lecture@{llm.provider}"
    # 该 user 的 tier（决定缓存键后缀）
    from app.services import student_portrait as portrait_service
    from app.workflows.learning_workflow import _ability_tier

    kp = db.get(KnowledgePoint, kp_id)
    portrait = portrait_service.get_portrait(db, user_id) if user_id else {"dimensions": []}
    tier = _ability_tier(portrait, kp_id, kp.name)
    kind = base_kind if tier is None else f"{base_kind}#{tier}"
    # 删除目标键，强制走最新管线重生成
    _delete_keys(db, kp_id, difficulty, kind)
    # 同时删独立图解缓存，使图解 Tab 也重生成
    _delete_keys(db, kp_id, "", "diagram")
    t0 = time.time()
    payload = resource_svc.generate_lecture(db, user_id, kp_id, difficulty)
    dt = time.time() - t0
    a = analyze(payload)
    a["_tier"] = tier
    a["_kind"] = kind
    a["_sec"] = round(dt, 1)
    return a


def cmd_validate(db) -> None:
    llm = get_llm()
    print(f"provider={llm.provider} is_mock={llm.is_mock}")
    print("单点验证：finetune·入门（此前被内容安全整篇拦截的坏缓存）")
    a = _regen_one(db, "finetune", "入门", BASE_USER)
    print(f"  tier={a['_tier']} kind={a['_kind']} {a['_sec']}s")
    print("  " + _fmt(a))
    print(f"  img_url={a['img_url']}")


def cmd_batch(db) -> None:
    llm = get_llm()
    print(f"provider={llm.provider} is_mock={llm.is_mock}")
    print(f"{'kp':12} {'diff':6} | metrics")
    print("-" * 110)
    results = {}
    for kp_id in CORE_KPS:
        for diff in DIFFS:
            try:
                a = _regen_one(db, kp_id, diff, BASE_USER)
                results[(kp_id, diff)] = a
                print(f"{kp_id:12} {diff:6} | {_fmt(a)} ({a['_sec']}s)")
            except Exception as exc:  # noqa: BLE001
                print(f"{kp_id:12} {diff:6} | ERROR: {type(exc).__name__}: {exc}")
            sys.stdout.flush()
    # 汇总
    print("\n== 汇总 ==")
    deg = [k for k, v in results.items() if v["degraded"]]
    nowiki = [k for k, v in results.items() if v["img_kind"] not in ("wikimedia",)]
    print(f"总计={len(results)}  误拦(degraded)={len(deg)} {deg}")
    print(f"非 Wikimedia 配图（走占位/其他/无）={len(nowiki)} {nowiki}")


def cmd_tier(db) -> None:
    """advanced vs beginner 深度对比（贴画像生效证据）。直接注入临时画像到两个用户。"""
    print("贴画像深度对比：对同一 (kp, 难度=初级) 用强/弱画像生成，比较深度")
    # 准备两个临时画像用户
    pairs = [("finetune", "微调"), ("nn", "神经网络")]
    for kp_id, _ in pairs:
        kp = db.get(KnowledgePoint, kp_id)
        for label, score, uid in [("advanced", 90, "u_10001"), ("beginner", 10, "u_10001")]:
            # 临时写画像
            p = db.get(StudentPortrait, uid)
            dims = [{"key": "ml", "label": kp.name, "value": "x", "score": score,
                     "confidence": 0.9, "source": "diagnostic", "updatedAt": "2026-06-30"}]
            if p is None:
                db.add(StudentPortrait(user_id=uid, dimensions=dims))
            else:
                p.dimensions = dims
            db.commit()
            a = _regen_one(db, kp_id, "初级", uid)
            print(f"  {kp_id:10} {label:9} tier={a['_tier']:8} kind={a['_kind']:26} len={a['len']:>6} "
                  f"h2={a['h2_count']} code={'Y' if a['code'] else 'n'} ({a['_sec']}s)")
    # 还原空画像
    p = db.get(StudentPortrait, "u_10001")
    if p is not None:
        p.dimensions = []
        db.commit()
    print("  （已还原 u_10001 为空画像）")


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    db = SessionLocal()
    try:
        {
            "inspect": cmd_inspect,
            "validate": cmd_validate,
            "clear": cmd_clear,
            "batch": cmd_batch,
            "tier": cmd_tier,
        }[cmd](db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
