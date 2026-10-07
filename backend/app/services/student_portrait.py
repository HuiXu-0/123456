"""异质学生动态画像存储服务（接口文档 17.2 / 17.3，C1-b）。

- 每用户一行 StudentPortrait（user_id PK），dimensions 存 PortraitDimension[]；
- get-or-create：首次访问落空画像（dimensions=[]），17.3「尚未开始诊断」占位，
  不视为错误；
- apply_updates：按 key 合并（随学随新）——同 key 字段级覆盖（新值覆盖旧值，
  未给字段保留旧值，如仅更新 value 不丢历史 score），新 key 追加；每维写
  updatedAt，整体 updated_at 刷新；维度按 PORTRAIT_DIMENSIONS 顺序稳定排列。

与 4.4 ability-portrait（固定 6 知识点雷达）并存、互不替换——本服务不读写
那条链路（_portrait_by_user 内存缓存），二者数据结构与用途均不同。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import PORTRAIT_DIMENSIONS, _sanitize_portrait_updates
from app.models.entities import StudentPortrait

logger = logging.getLogger(__name__)

_ORDER: dict[str, int] = {k: i for i, (k, _) in enumerate(PORTRAIT_DIMENSIONS)}


def _empty_portrait() -> dict[str, Any]:
    """干净空画像（17.3「尚未开始诊断」占位）：新用户/空数据/兜底均返回此结构。"""
    return {"dimensions": [], "version": "v1", "updatedAt": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(row: StudentPortrait) -> dict[str, Any]:
    """StudentPortrait → 接口文档 17.2 响应 data（camelCase）。"""
    return {
        "dimensions": list(row.dimensions or []),
        "version": row.version or "v1",
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _get_or_create(db: Session, user_id: str) -> StudentPortrait:
    """取/建画像行（幂等）。首次访问落空画像（dimensions=[]）。"""
    row = db.get(StudentPortrait, user_id)
    if row is None:
        row = StudentPortrait(user_id=user_id, dimensions=[], version="v1")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def get_portrait(db: Session, user_id: str) -> dict[str, Any]:
    """获取当前最新画像（接口文档 17.3）。无数据 → 空画像占位。

    防御：新用户首访 get-or-create 已落空画像；若遇空数据/瞬时库异常（如并发建行、
    旧库结构漂移），回落干净空态而非抛 500——保证「新用户调用恒得 200 空态」（任务4）。
    """
    try:
        return _serialize(_get_or_create(db, user_id))
    except Exception:  # noqa: BLE001 读接口降级：任何异常 → 200 空态，不向前端冒泡 500
        logger.warning("读取学生画像失败，回落空态（user_id=%s）", user_id, exc_info=True)
        db.rollback()
        return _empty_portrait()


def replace_portrait(
    db: Session, user_id: str, dimensions: list[dict[str, Any]]
) -> dict[str, Any]:
    """整份覆盖写入权威画像（接口文档 17.4「重做诊断 = 覆盖更新」）。

    简历 / 手动填写路径确认画像时调用：把已映射为 canonical 维度（与对话诊断同一套
    key）的整份画像写入同一行 StudentPortrait，**不与历史合并**（覆盖），从而三条
    路径产出的是同一份、同维度的权威画像；重做即覆盖、不分叉。
    维度按 PORTRAIT_DIMENSIONS 稳定排序，每维补 updatedAt。
    """
    row = _get_or_create(db, user_id)
    stamp = _now_iso()
    # 经统一契约清洗（C2）：附 kind/label、剥离偏好/主观维上的非法 score、归一 source，
    # 使三条路径（对话/简历/手动）产出**同一套三分类结构**的权威画像。
    cleaned: list[dict[str, Any]] = []
    for dim in _sanitize_portrait_updates(dimensions):
        merged = dict(dim)
        merged["updatedAt"] = stamp
        cleaned.append(merged)
    cleaned.sort(key=lambda d: _ORDER.get(d["key"], len(_ORDER)))
    row.dimensions = cleaned
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    _seed_ability_baseline(db, user_id, cleaned)
    return _serialize(row)


def _seed_ability_baseline(
    db: Session, user_id: str, cleaned: list[dict[str, Any]]
) -> None:
    """简历/手动路径：把能力维 knowledge_base 自陈分作低置信基线写入 Mastery（source=manual）。

    使「能力来源唯一 = Mastery」（C2 口径统一）——无微测数据的简历/手动路径，其自陈能力
    分透明地以 source=manual 落 Mastery，驱动 4.4 能力雷达 / 学情概览能力维；对话诊断
    重做时由 diagnostic 基线、真实 quiz 由 quiz 高置信覆盖同一行（重做即覆盖、不分叉）。
    单一 knowledge_base 概览分按知识点均匀铺基线（缺更细粒度数据时的保守基线）。
    """
    from app.services import knowledge_catalog  # 局部导入避免模块级环依赖
    from app.services import mastery as mastery_service

    kb = next(
        (d for d in cleaned if d["key"] == "knowledge_base" and isinstance(d.get("score"), int)),
        None,
    )
    if kb is None:
        return
    confidence = min(float(kb.get("confidence") or 0.5), 0.5)  # 自陈基线封顶低置信
    # 自陈基线仍只铺到 6 已验证基准点（不给 72 目录点写 Mastery，避免污染掌握度）
    for kp in knowledge_catalog.core_kps(db):
        mastery_service.set_baseline(
            db, user_id, kp.id, score=kb["score"], confidence=confidence, source="manual"
        )


def apply_updates(
    db: Session, user_id: str, updates: list[dict[str, Any]]
) -> dict[str, Any]:
    """合并画像维度增量并持久化（接口文档 17.2「随学随新」）。

    updates: 已经过 LLMClient 契约清洗的 PortraitDimension[]（无 updatedAt）。
    返回合并后的完整画像（17.2 结构）。
    """
    row = _get_or_create(db, user_id)
    by_key: dict[str, dict[str, Any]] = {d["key"]: dict(d) for d in (row.dimensions or [])}
    stamp = _now_iso()
    for upd in updates:
        key = upd.get("key")
        if not key:
            continue
        merged = by_key.get(key, {})
        # 字段级覆盖：新增量提供的字段覆盖旧值，未提供的字段保留（如仅更 value 不丢 score）
        merged.update({k: v for k, v in upd.items() if v is not None})
        merged["updatedAt"] = stamp
        by_key[key] = merged

    # 按固定维度顺序稳定排列（未知 key 落到末尾，保持插入序）
    dimensions = sorted(by_key.values(), key=lambda d: _ORDER.get(d["key"], len(_ORDER)))
    row.dimensions = dimensions
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _serialize(row)
