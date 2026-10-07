"""用户资源生成历史服务（「我的资源库」）。

两件事：
- ``record()``：生成/获取一类资源成功后**追加式埋点**（旁路，绝不打断生成主链路）。
  去重口径见 GenerationLog 文档——同一 (user_id,kp_id,kind,difficulty) upsert 刷新时间。
- ``list_history()``：按用户分页列出其资产，支持按 kind / 知识点 / 时间过滤，回 kpName。

kind 归一化：ResourceCache 的 kind 可能带 ``@provider`` / ``#tier`` 后缀（如
``lecture@deepseek#advanced``），本表只存基类（``lecture``），保证「一种资产一行」、
与切换 provider / 个性化档无关。非五类白名单的 kind 一律忽略（record 静默 no-op）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import GenerationLog, KnowledgePoint

logger = logging.getLogger(__name__)

# 五类可入库资产（归一化基类）→ 展示用中文形态名
KIND_LABELS: dict[str, str] = {
    "lecture": "定制讲义",
    "video": "讲解视频",
    "diagram": "知识图解",
    "mindmap": "思维导图",
    "code": "代码实操",
}
ALLOWED_KINDS = set(KIND_LABELS)

# 文档学习平行链路的形态白名单（比内置课程多「闪卡/练习题」两类；仅 record_document 用）
DOC_KIND_LABELS: dict[str, str] = {
    "lecture": "文档讲义",
    "video": "文档视频",
    "diagram": "文档图解",
    "mindmap": "文档导图",
    "quiz": "文档练习题",
    "flashcard": "文档闪卡",
}
DOC_ALLOWED_KINDS = set(DOC_KIND_LABELS)


def normalize_kind(kind: str) -> str:
    """剥离 ResourceCache 的 provider/tier 后缀，取基类（lecture@deepseek#advanced→lecture）。"""
    return (kind or "").split("@", 1)[0].split("#", 1)[0].strip()


def _default_title(db: Session, kp_id: str, kind: str, difficulty: str) -> str:
    """默认展示标题：「<知识点名> · <形态>[（难度）]」。"""
    kp = db.get(KnowledgePoint, kp_id)
    name = kp.name if kp else kp_id
    label = KIND_LABELS.get(kind, kind)
    return f"{name} · {label}（{difficulty}）" if difficulty else f"{name} · {label}"


def record(
    db: Session,
    user_id: str,
    kp_id: str,
    kind: str,
    difficulty: str = "",
    *,
    title: str | None = None,
    resource_ref: str | None = None,
) -> None:
    """追加式埋点（upsert）。**任何异常都吞掉**——埋点失败绝不能拖垮资源生成主链路。

    - kind 归一化到五类基类，非白名单直接忽略；
    - 命中 (user_id,kp_id,kind,difficulty) → 刷新 created_at（+可选 title/ref），
      否则插入新行；
    - 用 flush 探测唯一冲突（并发下双插入），冲突则回退为更新既有行。
    """
    if not user_id or not kp_id:
        return
    base_kind = normalize_kind(kind)
    if base_kind not in ALLOWED_KINDS:
        return
    diff = difficulty or ""
    try:
        row = (
            db.query(GenerationLog)
            .filter(
                GenerationLog.user_id == user_id,
                GenerationLog.kp_id == kp_id,
                GenerationLog.kind == base_kind,
                GenerationLog.difficulty == diff,
            )
            .one_or_none()
        )
        now = datetime.now(timezone.utc)
        ref = resource_ref if resource_ref is not None else f"{base_kind}:{kp_id}:{diff}"
        ttl = title if title else _default_title(db, kp_id, base_kind, diff)
        if row is not None:
            row.created_at = now  # 最近生成时间
            row.title = ttl
            row.resource_ref = ref
        else:
            db.add(
                GenerationLog(
                    user_id=user_id,
                    kp_id=kp_id,
                    kind=base_kind,
                    difficulty=diff,
                    title=ttl,
                    resource_ref=ref,
                    created_at=now,
                )
            )
        db.commit()
    except Exception as exc:  # noqa: BLE001  埋点旁路：失败仅告警，不外抛
        db.rollback()
        logger.warning("[generation_log] 埋点失败（已忽略）：%s", exc)


def record_document(
    db: Session,
    user_id: str,
    doc_id: str,
    doc_title: str,
    kind: str,
    *,
    difficulty: str = "",
    title: str | None = None,
    artifact: dict | None = None,
) -> None:
    """文档学习资源埋点（追加式 upsert，与内置课程 record() 平行、互不影响）。

    写入同一 GenerationLog 表但带 source="document" + doc_id/doc_title 来源标识，
    进「我的资源库」。去重口径：同一 (user_id, kp_id=doc_id, kind, difficulty)——
    kp_id 复用为 doc_id（不同文档不同 id → 不与内置课程行冲突）。**任何异常都吞掉**。

    产物落库：``artifact`` 传入时把产物**实际内容**（完整响应体）随行持久化——生成/
    重新生成时写入并刷新 artifact_updated_at，供后续「查看」直接读、不再重跑生成。
    ``artifact=None``（纯埋点旧调用）不清空既有产物（幂等、向后兼容）。
    """
    if not user_id or not doc_id:
        return
    base_kind = (kind or "").strip()
    if base_kind not in DOC_ALLOWED_KINDS:
        return
    diff = difficulty or ""
    ttl = title or f"{doc_title} · {DOC_KIND_LABELS.get(base_kind, base_kind)}"
    try:
        row = (
            db.query(GenerationLog)
            .filter(
                GenerationLog.user_id == user_id,
                GenerationLog.kp_id == doc_id,
                GenerationLog.kind == base_kind,
                GenerationLog.difficulty == diff,
                GenerationLog.source == "document",
            )
            .one_or_none()
        )
        now = datetime.now(timezone.utc)
        ref = f"document:{doc_id}:{base_kind}:{diff}"
        if row is not None:
            row.created_at = now
            row.title = ttl
            row.resource_ref = ref
            row.doc_title = doc_title
            if artifact is not None:  # 不覆盖既有产物（纯埋点调用）
                row.artifact = artifact
                row.artifact_updated_at = now
        else:
            db.add(
                GenerationLog(
                    user_id=user_id,
                    kp_id=doc_id,
                    kind=base_kind,
                    difficulty=diff,
                    title=ttl,
                    resource_ref=ref,
                    source="document",
                    doc_id=doc_id,
                    doc_title=doc_title,
                    created_at=now,
                    artifact=artifact,
                    artifact_updated_at=now if artifact is not None else None,
                )
            )
        db.commit()
    except Exception as exc:  # noqa: BLE001  埋点旁路：失败仅告警，不外抛
        db.rollback()
        logger.warning("[generation_log] 文档埋点失败（已忽略）：%s", exc)


def get_document_artifact(
    db: Session, user_id: str, doc_id: str, kind: str, difficulty: str = ""
) -> dict | None:
    """读取已落库的文档学习产物（供「查看」直接渲染，不触发任何生成）。

    按 (user_id, kp_id=doc_id, kind, difficulty, source="document") 定位资产行；
    命中且 artifact 已物化 → 返回其完整响应体副本，否则 None（调用方回退实时生成）。
    **归属由 user_id 过滤强约束**：只会返回本人产物，不越权读他人资产。
    """
    if not user_id or not doc_id:
        return None
    base_kind = (kind or "").strip()
    diff = difficulty or ""
    try:
        row = (
            db.query(GenerationLog)
            .filter(
                GenerationLog.user_id == user_id,
                GenerationLog.kp_id == doc_id,
                GenerationLog.kind == base_kind,
                GenerationLog.difficulty == diff,
                GenerationLog.source == "document",
            )
            .one_or_none()
        )
    except Exception as exc:  # noqa: BLE001  读产物失败 → 回退生成（不外抛）
        logger.warning("[generation_log] 读产物失败（回退生成）：%s", exc)
        return None
    if row is None or row.artifact is None:
        return None
    return dict(row.artifact)


def _row_to_item(db: Session, r: GenerationLog) -> dict[str, Any]:
    """单行 → 资源库条目（契约 camelCase，供重命名回包；口径与 list_history 一致）。"""
    is_doc = r.source == "document"
    kp = None if is_doc else db.get(KnowledgePoint, r.kp_id)
    return {
        "id": r.id,
        "kpId": r.kp_id,
        "kpName": (r.doc_title or r.kp_id) if is_doc else (kp.name if kp else r.kp_id),
        "kind": r.kind,
        "difficulty": r.difficulty,
        "title": r.title,
        "resourceRef": r.resource_ref,
        "source": "document" if is_doc else "builtin",
        "docId": r.doc_id,
        "docTitle": r.doc_title,
        "createdAt": _iso_utc(r.created_at),
    }


def rename(db: Session, user_id: str, log_id: int, title: str) -> dict[str, Any] | None:
    """重命名一条资源库资产的展示标题（改，CRUD）。归属校验：非本人/不存在 → None。"""
    row = db.get(GenerationLog, log_id)
    if row is None or row.user_id != user_id:
        return None
    new_title = (title or "").strip()
    if new_title:
        row.title = new_title[:256]
        db.commit()
    return _row_to_item(db, row)


def delete(db: Session, user_id: str, log_id: int) -> bool:
    """删除一条资源库资产（删，CRUD；连带其已落库产物）。归属校验：非本人/不存在 → False。"""
    row = db.get(GenerationLog, log_id)
    if row is None or row.user_id != user_id:
        return False
    db.delete(row)
    db.commit()
    return True


def delete_document_rows(db: Session, user_id: str, doc_id: str) -> int:
    """删除某文档在资源库的全部资产行（连带产物）——文档被删时旁路清理，避免孤儿资产。

    **best-effort**：失败仅告警、不外抛（绝不拖垮文档删除主链路）。返回清理行数。
    """
    if not user_id or not doc_id:
        return 0
    try:
        rows = (
            db.query(GenerationLog)
            .filter(
                GenerationLog.user_id == user_id,
                GenerationLog.kp_id == doc_id,
                GenerationLog.source == "document",
            )
            .all()
        )
        for r in rows:
            db.delete(r)
        db.commit()
        return len(rows)
    except Exception as exc:  # noqa: BLE001  清理旁路：失败仅告警
        db.rollback()
        logger.warning("[generation_log] 文档资产清理失败（已忽略）：%s", exc)
        return 0


def _iso_utc(dt: datetime | None) -> str | None:
    """把落库的（naive）UTC 时间显式标注为 UTC 再 isoformat，避免前端按本地时区误解出现时差。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_time(value: str | None) -> datetime | None:
    """宽松解析时间过滤参数：接受 ISO8601（含带 Z）或 YYYY-MM-DD。解析失败→None（不过滤）。"""
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    parsed: datetime | None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.strptime(value.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    # 落库列为 naive-UTC；带时区的入参统一折算到 UTC 再去 tz，保证与列同口径可比
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def list_history(
    db: Session,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    kind: str | None = None,
    kp_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """分页列出当前用户的资源生成历史（按生成时间倒序），附知识点名/文档来源标识。

    过滤：kind / kpId / 时间区间 [startTime, endTime] / source（builtin|document，缺省不限）。
    返回契约字段 camelCase；新增 source/docId/docTitle（内置课程行 source="builtin"）。
    """
    q = db.query(GenerationLog).filter(GenerationLog.user_id == user_id)
    base_kind = normalize_kind(kind) if kind else None
    if base_kind in ALLOWED_KINDS or base_kind in DOC_ALLOWED_KINDS:
        q = q.filter(GenerationLog.kind == base_kind)
    if kp_id:
        q = q.filter(GenerationLog.kp_id == kp_id)
    if source == "document":
        q = q.filter(GenerationLog.source == "document")
    elif source == "builtin":
        q = q.filter((GenerationLog.source.is_(None)) | (GenerationLog.source != "document"))
    start = _parse_time(start_time)
    end = _parse_time(end_time)
    if start is not None:
        q = q.filter(GenerationLog.created_at >= start)
    if end is not None:
        q = q.filter(GenerationLog.created_at <= end)

    total = q.count()
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    rows = (
        q.order_by(GenerationLog.created_at.desc(), GenerationLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    # 一次取全部知识点名映射，避免逐行查库
    names = {kp.id: kp.name for kp in db.query(KnowledgePoint).all()}
    items = []
    for r in rows:
        is_doc = r.source == "document"
        items.append(
            {
                "id": r.id,
                "kpId": r.kp_id,
                # 文档行用文档标题作展示名（kp_id=doc_id 不在知识点表中）；内置行取知识点名
                "kpName": (r.doc_title or r.kp_id) if is_doc else names.get(r.kp_id, r.kp_id),
                "kind": r.kind,
                "difficulty": r.difficulty,
                "title": r.title,
                "resourceRef": r.resource_ref,
                "source": "document" if is_doc else "builtin",
                "docId": r.doc_id,
                "docTitle": r.doc_title,
                "createdAt": _iso_utc(r.created_at),
            }
        )
    return {"items": items, "total": total, "page": page, "pageSize": page_size}
