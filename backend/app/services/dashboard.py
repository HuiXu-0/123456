"""学情概览聚合服务（B6；C1-c 真实化；C2 能力/偏好分轴重构）。

覆盖接口文档 12.1：GET /dashboard/overview——为减少首屏请求数做后端聚合。
**全部数值由该用户真实数据派生（禁止臆造）**，与前端 services/dashboard.ts 的
`synthesizeOverview` 逐位同口径。

C2 关键修复——**能力打分、偏好归类型，不再混进同一 0-100 雷达轴**：
- radar / 优势 / 盲区 / 综合分 / 水平 ← **能力维**（各知识点掌握度），由诊断微测 / 真实
  quiz 写入 Mastery 的能力分驱动（= 4.4 能力雷达同源，口径统一）：
    - 轴值：Mastery 实测分；passed 无分→80、learning/pending→50、未测→0（不臆造）；
    - 优势：得分高的知识点（降序前 3）；盲区：得分 <60 或未测（升序前 3）；
    - 综合分：各能力轴均值（保留 1 位）；水平：分档（<40 初学/<60 入门/<80 中级/≥80 高级）；
    - 未诊断/未测 → 能力轴全 0、综合分 0、水平「初学」（不杜撰）。
- preferences（新增）← StudentPortrait 偏好维（认知风格/学习节奏/易错倾向），**以类型标签
  呈现、无分数、不上雷达轴**（修复「学习节奏哪有满分」的混轴别扭）。
- 知识图谱覆盖率 / 已学资源 ← 真实 Mastery（已通过核心知识点占比 / 计数），新用户恒 0。
- comparison.betterThanPct ← 综合分单调映射；targetSummary ← Journey（7.4 同源）。

口径权威：前端 synthesizeOverview。能力雷达轴次序对齐 4.4 ABILITY_DIMENSIONS（知识点
lesson_seq 升序），保证「确认弹窗」与「首页 overview」逐项一致。
"""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import PREFERENCE_DIM_KEYS
from app.models.entities import Journey, StudentPortrait, User
from app.services import knowledge_catalog
from app.services import mastery as mastery_service
from app.services import profile as profile_service

# betterThanPct 标定系数（演示性相对位次；综合分为 0 时即 0，不杜撰）
_BETTER_THAN_FACTOR = 1.365


def _round_half_up(x: float) -> int:
    """JS Math.round 语义（正数 .5 向上），与前端 synthesizeOverview 逐位对齐。"""
    return math.floor(x + 0.5)


def _is_tested(entry: dict[str, Any] | None) -> bool:
    """该知识点是否已被实测：有能力分（微测/quiz/手动基线写入）或已通过测验。"""
    if entry is None:
        return False
    if entry.get("score") is not None:
        return True
    return entry.get("status") == mastery_service.STATUS_PASSED


def _level_for(score: float) -> str:
    """综合分 → 水平档（前端 synthesizeOverview 同阈值）。"""
    if score >= 80:
        return "高级"
    if score >= 60:
        return "中级"
    if score >= 40:
        return "入门"
    return "初学"


def _preferences(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从画像中提取偏好维 → 类型标签（无分数、不上轴）。"""
    by_key = {d.get("key"): d for d in dimensions if d.get("key")}
    out: list[dict[str, Any]] = []
    for key in PREFERENCE_DIM_KEYS:
        dim = by_key.get(key)
        if not dim:
            continue
        out.append(
            {
                "key": key,
                "label": dim.get("label") or key,
                "value": dim.get("value") or "",  # 类型中文标签（如「图像型」）
                "optionKey": dim.get("optionKey"),
            }
        )
    return out


def overview(db: Session, user: User) -> dict[str, Any]:
    """学情概览聚合（接口文档 12.1）。能力靠测打分、偏好归类型（见模块文档）。"""
    # 能力雷达：与 4.4 同源（Mastery 实测能力分驱动，按知识点 lesson_seq 升序）
    ability = profile_service.ability_portrait(db, user)
    score_map = mastery_service.get_score_map(db, user.id)
    # 能力雷达/优势盲区仍以 6 已验证基准点为轴（与 4.4 同源；体系扩到 78 点不改）
    kps = sorted(knowledge_catalog.core_kps(db), key=lambda k: k.lesson_seq)
    scored = [
        {"name": name, "mastery": value, "tested": _is_tested(score_map.get(kp.id))}
        for name, value, kp in zip(ability["dimensions"], ability["values"], kps)
    ]
    radar = {"dimensions": ability["dimensions"], "values": ability["values"]}

    # 优势 / 盲区只在**已测**知识点上判定（未测 ≠ 盲区，不臆造强弱项）：
    # 优势 = 已测且得分高（降序前 3）；盲区 = 已测且 <60（升序前 3）。
    tested = [s for s in scored if s["tested"]]
    strong_topics = [
        {"name": s["name"], "mastery": s["mastery"]}
        for s in sorted(tested, key=lambda s: -s["mastery"])
    ][:3]
    weak_topics = [
        {"name": s["name"], "mastery": s["mastery"]}
        for s in sorted((s for s in tested if s["mastery"] < 60), key=lambda s: s["mastery"])
    ][:3]

    # 综合分：各能力轴均值（前端 Math.round(mean*10)/10 同口径）；无能力轴 → 0
    overall_score = (
        _round_half_up(sum(s["mastery"] for s in scored) / len(scored) * 10) / 10
        if scored
        else 0.0
    )

    # 偏好画像：类型标签（不打分、不上轴）
    row = db.get(StudentPortrait, user.id)
    dimensions: list[dict[str, Any]] = list(row.dimensions or []) if row else []
    preferences = _preferences(dimensions)

    # 知识图谱覆盖率 / 已学资源：真实 Mastery 派生（已通过核心知识点占比 / 计数）。
    # 分母恒为 6 已验证基准点（体系扩到 78 点不虚增分母、不稀释覆盖率）。
    core_kp_ids = knowledge_catalog.core_kp_ids(db)
    status_map = mastery_service.get_status_map(db, user.id)
    passed_core = sum(
        1 for kid in core_kp_ids if status_map.get(kid) == mastery_service.STATUS_PASSED
    )
    coverage = round(passed_core / len(core_kp_ids), 2) if core_kp_ids else 0.0

    journey = db.get(Journey, user.id)

    return {
        "overall_level": _level_for(overall_score),
        "overall_score": overall_score,
        "knowledge_graph_coverage": coverage,
        "learned_resources": passed_core,
        "strong_topics": strong_topics,
        "weak_topics": weak_topics,
        "radar": radar,
        "preferences": preferences,
        "comparison": {
            "betterThanPct": min(99, round(overall_score * _BETTER_THAN_FACTOR))
        },
        "targetSummary": {  # 与 7.4 Journey 同源
            "hasDiagnosed": bool(journey and journey.has_diagnosed),
            "targetJobName": journey.target_job_name if journey else None,
            "matchPct": journey.match_pct if journey else None,
        },
    }
