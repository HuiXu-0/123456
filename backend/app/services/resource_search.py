"""外部资源·联网搜索聚合 Agent（接口文档 8.6 增量，C-fix 批3-bonus）。

把"资源推荐"从静态种子库升级为 **AI 联网搜索聚合**：
1. 按当前知识点 + 用户薄弱点构造检索式；
2. 经可插拔 `web_search` Provider **联网搜索**（无能力/失败 → 回落 ExternalResource 种子作
   候选池，`online=False`，保证可跑）；
3. 候选交 **聚合 Agent（LLMClient.aggregate_resources）整理 + critic 评分**（相关度/可信度/
   推荐理由），按相关度降序返回 8.6 同结构清单（前端 ResourceAggregator 直接渲染）。

后端代理联网，规避前端配额/跨域。薄弱点缺省由该用户真实 Mastery（未通过知识点）派生，
使推荐**因人而异**。
"""
from __future__ import annotations

import logging
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import get_llm
from app.core.config import settings
from app.models.entities import ExternalResource, ExternalResourceCache
from app.services import knowledge_catalog
from app.services import mastery as mastery_service
from app.services import resource as resource_service
from app.services import web_search

logger = logging.getLogger("app.services.resource_search")


def _utcnow_naive() -> datetime:
    """SQLite DateTime 统一使用 UTC naive，避免本地时区与 aware 比较混用。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cache_key(kp_id: str, provider: str, query: str) -> str:
    raw = f"{kp_id}\0{provider}\0{query}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _decode_cached(row: ExternalResourceCache | None) -> list[dict[str, Any]]:
    if row is None:
        return []
    try:
        value = json.loads(row.items_json)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _find_cache(
    db: Session, kp_id: str, provider: str, query: str
) -> tuple[list[dict[str, Any]], bool]:
    """返回 (items, fresh)。精确缓存优先；否则复用该知识点最近一次真实搜索结果。"""
    row = db.get(ExternalResourceCache, _cache_key(kp_id, provider, query))
    if row is None:
        row = (
            db.query(ExternalResourceCache)
            .filter(ExternalResourceCache.kp_id == kp_id)
            .order_by(ExternalResourceCache.fetched_at.desc())
            .first()
        )
    items = _decode_cached(row)
    fresh = bool(items and row and row.expires_at > _utcnow_naive())
    return items, fresh


def _save_cache(
    db: Session,
    kp_id: str,
    provider: str,
    query: str,
    items: list[dict[str, Any]],
) -> None:
    if not items:
        return
    now = _utcnow_naive()
    key = _cache_key(kp_id, provider, query)
    row = db.get(ExternalResourceCache, key)
    if row is None:
        row = ExternalResourceCache(cache_key=key, kp_id=kp_id, provider=provider, query=query)
        db.add(row)
    row.items_json = json.dumps(items, ensure_ascii=False)
    row.fetched_at = now
    row.expires_at = now + timedelta(seconds=max(60, settings.external_resource_cache_ttl_seconds))
    try:
        db.commit()
    except Exception:  # noqa: BLE001 缓存是增强项，不能让已取得的真实结果失败
        db.rollback()
        logger.warning("外部资源缓存写入失败（kp=%s provider=%s）", kp_id, provider, exc_info=True)


def _derive_weak_points(db: Session, user_id: str, kp_id: str) -> list[str]:
    """缺省薄弱点：该用户未通过的核心知识点名（含当前 kp），最多 4 个。"""
    status_map = mastery_service.get_status_map(db, user_id)
    # 缺省薄弱点仍在 6 已验证基准点内派生（体系扩到 78 点不改变默认薄弱点口径）
    kps = sorted(knowledge_catalog.core_kps(db), key=lambda k: k.lesson_seq)
    weak = [
        kp.name
        for kp in kps
        if status_map.get(kp.id) != mastery_service.STATUS_PASSED
    ]
    cur = next((kp.name for kp in kps if kp.id == kp_id), None)
    if cur and cur not in weak:
        weak.insert(0, cur)
    return weak[:4]


def _seed_candidates(db: Session, kp_id: str) -> list[dict[str, Any]]:
    """种子兜底候选池（无联网能力时）：ExternalResource 精选库 → 候选结构。

    体系拓展点（78 点目录中无专属种子的 kp）回落全库精选：保证任意在库 kp 的
    推荐非空且候选池含视频（形态保底可生效），最终排序仍由聚合评分决定。
    """
    rows = (
        db.query(ExternalResource)
        .filter(ExternalResource.kp_id == kp_id)
        .order_by(ExternalResource.relevance.desc(), ExternalResource.id)
        .all()
    )
    if not rows:
        rows = (
            db.query(ExternalResource)
            .order_by(ExternalResource.relevance.desc(), ExternalResource.id)
            .limit(12)
            .all()
        )
    candidates: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {
            "title": r.title,
            "url": r.url,
            "source": r.source,
            "snippet": r.reason or "",
            "type": r.type,
        }
        if r.embed:
            item["embed"] = r.embed
        if r.duration:
            item["duration"] = r.duration
        candidates.append(item)
    return candidates


def aggregate(
    db: Session,
    user_id: str,
    kp_id: str,
    query: str | None = None,
    weak_points: list[str] | None = None,
) -> dict[str, Any]:
    """联网搜索聚合外部资源（接口文档 8.6 增量）。知识点不存在 → 抛 UnknownKnowledgePoint。"""
    kp = resource_service._require_kp(db, kp_id)
    weak = [w for w in (weak_points or []) if isinstance(w, str) and w.strip()] or \
        _derive_weak_points(db, user_id, kp_id)
    search_query = (query or "").strip() or f"{kp.name} {' '.join(weak[:3])} 教程 讲解 论文".strip()

    provider = web_search.get_provider()
    cached_items, cache_fresh = _find_cache(db, kp_id, provider.name, search_query)
    if provider.online and cache_fresh:
        return {
            "kpId": kp.id,
            "kpName": kp.name,
            "provider": provider.name,
            "online": True,
            "items": cached_items,
        }

    hits = provider.search(
        search_query, max_results=settings.search_max_results
    ) if provider.online else []
    online = bool(hits)
    if not hits and cached_items:
        # 当前联网失败/无 Provider：优先返回最近真实结果，online=false 明示本次未联网。
        return {
            "kpId": kp.id,
            "kpName": kp.name,
            "provider": provider.name,
            "online": False,
            "items": cached_items,
        }
    if not hits:  # 无联网能力 / 联网失败且无缓存 → 种子兜底候选池
        hits = _seed_candidates(db, kp_id)
    elif not any(h.get("type") == "视频" for h in hits):
        # 视频保底（候选池层）：联网命中无视频（通用搜索常不返回视频站点）→
        # 从精选种子库补充视频候选（真实 URL、可站内嵌播），最终取舍仍由聚合评分决定。
        hit_urls = {str(h.get("url")) for h in hits}
        seed_videos = [
            c for c in _seed_candidates(db, kp_id)
            if c.get("type") == "视频" and str(c.get("url")) not in hit_urls
        ][:2]
        if seed_videos:
            hits = hits + seed_videos
            logger.info(
                "外部资源聚合：联网命中无视频，补充精选库视频候选 %d 条（kp=%s）",
                len(seed_videos), kp_id,
            )
        else:
            logger.info("外部资源聚合：联网命中与精选库均无视频候选，视频保底降级（kp=%s）", kp_id)

    items = get_llm().aggregate_resources(kp.name, weak, hits)
    if online:
        _save_cache(db, kp_id, provider.name, search_query, items)
    return {
        "kpId": kp.id,
        "kpName": kp.name,
        "provider": provider.name,
        "online": online,
        "items": items,
    }
