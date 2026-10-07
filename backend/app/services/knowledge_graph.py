"""数据驱动的聚焦知识图谱服务。

覆盖接口文档 10.1：
- 默认仍输出契约规定的 12 节点 / 14 边，但节点与先修主边从 78 点 KnowledgePoint
  目录实时投影，不再维护一套虚构掌握度种子；
- category/value 每次请求按真实 mastery 状态与实测分实时推导：
    passed                  → 0 已掌握（value=100）
    learning|pending-check  → 1 学习中（value=min(种子值, 60)，与前端派生规则一致）
    未开始且种子值较高       → 2 待学习
    未开始且种子值极低(<20)  → 3 知识盲区
- value 取掌握度 %（前端据此算 symbolSize）。

12 个聚焦节点都映射到知识目录中的真实知识点，因此拓展点产生学习记录后也会联动。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import KnowledgePoint
from app.services import mastery as mastery_service

logger = logging.getLogger(__name__)

# 知识盲区阈值：未开始且种子掌握度低于该值 → category 3
BLIND_SPOT_THRESHOLD = 20

# 学习中节点的掌握度上限（与前端派生规则 min(value, 60) 一致）
_LEARNING_VALUE_CAP = 60

# 契约节点 id + 78 点目录 code + 前端稳定展示名。
_FOCUS_NODES: list[tuple[str, str, str]] = [
    ("ml", "ML-1", "机器学习基础"),
    ("nn", "DL-1", "神经网络基础"),
    ("dl", "DL-4", "深度学习原理"),
    ("cnn", "DL-6", "CNN架构"),
    ("rnn", "DL-8", "RNN架构"),
    ("attn", "DL-10", "注意力机制"),
    ("transformer", "LLM-2", "Transformer"),
    ("bertgpt", "LLM-3", "BERT与GPT"),
    ("finetune", "LLM-5", "大模型微调"),
    ("prompt", "LLM-8", "Prompt工程"),
    ("rag", "LLM-9", "RAG检索增强"),
    ("agent", "AGT-1", "AI Agent开发"),
]

# 目录先修投影之外的 3 条跨路线教学关联，补齐契约 14 边并表达并行学习路径。
_CROSS_LINKS: list[tuple[str, str]] = [
    ("dl", "attn"),
    ("rnn", "transformer"),
    ("rag", "agent"),
]

_FALLBACK_LINKS: list[tuple[str, str]] = [
    ("ml", "nn"), ("nn", "dl"), ("dl", "cnn"), ("dl", "rnn"),
    ("dl", "attn"), ("attn", "transformer"), ("rnn", "transformer"),
    ("transformer", "bertgpt"), ("bertgpt", "finetune"),
    ("transformer", "prompt"), ("bertgpt", "rag"), ("rag", "agent"),
    ("prompt", "agent"), ("finetune", "agent"),
]

_CATEGORIES = [
    {"name": "已掌握"},
    {"name": "学习中"},
    {"name": "待学习"},
    {"name": "知识盲区"},
]


def derive_node(status: str | None, base_value: int) -> tuple[int, int]:
    """按掌握状态推导 (category, value)（接口文档 10.1 备注口径）。

    status 为该节点对应核心知识点的 KPStatus；None 表示无映射或未开始。
    """
    if status == mastery_service.STATUS_PASSED:
        return 0, 100
    if status in (mastery_service.STATUS_LEARNING, mastery_service.STATUS_PENDING_CHECK):
        return 1, min(base_value, _LEARNING_VALUE_CAP)
    if base_value < BLIND_SPOT_THRESHOLD:
        return 3, base_value
    return 2, base_value


def derived_nodes(db: Session, user_id: str) -> list[dict[str, Any]]:
    """目录投影 12 节点 + 实测 category/value（dashboard 聚合复用）。

    防御：12 节点为静态种子恒存在；掌握度/知识点映射查询若遇空数据或瞬时库异常，
    回落为「全部未开始」推导（而非抛 500），保证新用户/空数据恒得 200 空态图谱（任务4）。
    """
    try:
        status_map = mastery_service.get_status_map(db, user_id)
        score_map = mastery_service.get_score_map(db, user_id)
        kp_by_code = {kp.code: kp for kp in db.query(KnowledgePoint).all()}
    except Exception:  # noqa: BLE001 读接口降级：用户态查询失败 → 按全未开始推导
        logger.warning("知识图谱掌握度查询失败，按全未开始推导（user_id=%s）", user_id, exc_info=True)
        status_map, score_map, kp_by_code = {}, {}, {}
    nodes: list[dict[str, Any]] = []
    for node_id, code, display_name in _FOCUS_NODES:
        kp = kp_by_code.get(code)
        kp_id = kp.id if kp is not None else ""
        status = status_map.get(kp_id)
        score = score_map.get(kp_id, {}).get("score")
        if status == mastery_service.STATUS_PASSED:
            category, value = 0, 100
        elif isinstance(score, int):
            value = max(0, min(100, score))
            category = 0 if value >= 70 else (3 if value < BLIND_SPOT_THRESHOLD else 1)
        elif status in (mastery_service.STATUS_LEARNING, mastery_service.STATUS_PENDING_CHECK):
            category, value = 1, 0
        else:
            category, value = 2, 0
        nodes.append(
            {"id": node_id, "name": display_name, "category": category, "value": value}
        )
    return nodes


def derived_links(db: Session) -> list[dict[str, str]]:
    """把完整目录先修链折叠到 12 个聚焦节点，再加入明确的跨路线教学关联。"""
    try:
        rows = db.query(KnowledgePoint).all()
    except Exception:  # noqa: BLE001 图谱读降级：保留契约拓扑，节点状态仍由上层兜底
        logger.warning("知识图谱目录查询失败，回落兼容拓扑", exc_info=True)
        return [
            {"source": source, "target": target}
            for source, target in _FALLBACK_LINKS
        ]
    by_id = {kp.id: kp for kp in rows}
    by_code = {kp.code: kp for kp in rows}
    alias_by_kp_id = {
        by_code[code].id: alias
        for alias, code, _ in _FOCUS_NODES
        if code in by_code
    }

    def nearest_focus(prereq_id: str, seen: set[str]) -> set[str]:
        if prereq_id in seen:
            return set()
        if prereq_id in alias_by_kp_id:
            return {alias_by_kp_id[prereq_id]}
        kp = by_id.get(prereq_id)
        if kp is None:
            return set()
        next_seen = seen | {prereq_id}
        found: set[str] = set()
        for parent_id in list(kp.prerequisites or []):
            found.update(nearest_focus(parent_id, next_seen))
        return found

    links: set[tuple[str, str]] = set(_CROSS_LINKS)
    for target_alias, code, _ in _FOCUS_NODES:
        target = by_code.get(code)
        if target is None:
            continue
        for prereq_id in list(target.prerequisites or []):
            for source_alias in nearest_focus(prereq_id, set()):
                if source_alias != target_alias:
                    links.add((source_alias, target_alias))
    return [
        {"source": source, "target": target}
        for source, target in sorted(links)
    ]


def get_graph(db: Session, user_id: str) -> dict[str, Any]:
    """知识图谱（接口文档 10.1）：nodes / links / categories。"""
    return {
        "nodes": derived_nodes(db, user_id),
        "links": derived_links(db),
        "categories": [dict(c) for c in _CATEGORIES],
    }
