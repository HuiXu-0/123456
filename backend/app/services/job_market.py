"""岗位市场服务（B6）。

覆盖接口文档第 5 章 / 15.5 数据管线约定：
- 5.1 hot_jobs：热门岗位列表 [{id, name}]，固定契约顺序（种子 sort_order）。
- 5.2 get_snapshot：岗位市场快照（2.4 JobMarket 完整结构）。
  - demo 阶段：后端托管预置快照（种子自 frontend/public/data/job-market/*.json），
    JobSnapshot 表即数据源；
  - 降级（15.5）：实时数据源不可用（settings.job_market_offline 模拟故障开关）→
    返回最近快照并置 code 2002 / data.offline=true（HTTP 200），对齐前端
    JobMarketResult.offline「离线快照」标记；
  - TASK-006-G：支持可信采集器 HTTP feed / 本地目录刷新、严格契约校验和只新不旧；
    快照过期时如实标记 offline，不把历史样本冒充实时数据。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.llm import ABILITY_DIMENSIONS, _MOCK_BASELINE
from app.models.entities import JobSnapshot, User
from app.services import mastery as mastery_service
from app.services import profile as profile_service


class UnknownJob(Exception):
    """岗位不存在（→ code 1004 / 404）。"""


_JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_HEAT_VALUES = {"极高", "高", "中"}


def _parse_time(value: Any) -> datetime:
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"fetchedAt 不是 ISO-8601 时间: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError("fetchedAt 必须携带时区")
    return parsed.astimezone(timezone.utc)


def validate_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """严格校验采集器输出，防止脏数据覆盖最近可信快照。"""
    required = {
        "id", "name", "salaryRange", "salaryMedian", "heat", "heatPct",
        "openings", "source", "fetchedAt", "skills", "radar",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"岗位快照缺字段: {sorted(missing)}")
    extra = set(payload) - required
    if extra:
        raise ValueError(f"岗位快照含未定义字段: {sorted(extra)}")
    job_id = str(payload["id"]).strip()
    if not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError(f"岗位 id 非法: {job_id}")
    if not str(payload["name"]).strip() or not str(payload["source"]).strip():
        raise ValueError("岗位名称和真实来源不能为空")
    if payload["heat"] not in _HEAT_VALUES:
        raise ValueError(f"岗位热度枚举非法: {payload['heat']}")
    if not isinstance(payload["heatPct"], int) or not 0 <= payload["heatPct"] <= 100:
        raise ValueError("heatPct 必须是 0-100 整数")
    if not isinstance(payload["openings"], int) or payload["openings"] < 0:
        raise ValueError("openings 必须是非负整数")
    _parse_time(payload["fetchedAt"])
    if not isinstance(payload["skills"], list) or not payload["skills"]:
        raise ValueError("skills 必须是非空数组")
    for skill in payload["skills"]:
        if (
            not isinstance(skill, dict)
            or not str(skill.get("name") or "").strip()
            or not isinstance(skill.get("freqPct"), int)
            or not 0 <= skill["freqPct"] <= 100
        ):
            raise ValueError("skills 项必须包含 name 与 0-100 整数 freqPct")
    radar = payload["radar"]
    if not isinstance(radar, dict) or set(radar) != set(ABILITY_DIMENSIONS):
        raise ValueError("radar 必须严格包含固定 6 个能力维度")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100
        for value in radar.values()
    ):
        raise ValueError("radar 能力值必须是 0-100 整数")
    return dict(payload)


def refresh_snapshots(db: Session, payloads: list[dict[str, Any]]) -> dict[str, int]:
    """原子校验并刷新较新的岗位快照；旧数据不会覆盖新数据。"""
    validated = [validate_snapshot(payload) for payload in payloads]
    updated = 0
    skipped = 0
    max_order = max((row.sort_order for row in db.query(JobSnapshot).all()), default=-1)
    for payload in validated:
        job_id = payload["id"]
        incoming_at = _parse_time(payload["fetchedAt"])
        row = db.get(JobSnapshot, job_id)
        if row is not None:
            current_raw = (row.payload or {}).get("fetchedAt")
            if current_raw and _parse_time(current_raw) >= incoming_at:
                skipped += 1
                continue
        else:
            max_order += 1
            row = JobSnapshot(id=job_id, name=payload["name"], payload={}, sort_order=max_order)
            db.add(row)
        row.name = payload["name"]
        row.payload = payload
        row.fetched_at = incoming_at.replace(tzinfo=None)
        updated += 1
    db.commit()
    return {"updated": updated, "skipped": skipped, "total": len(validated)}


def refresh_from_directory(db: Session, directory: str | Path) -> dict[str, int]:
    source_dir = Path(directory)
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(source_dir.glob("*.json"))
    ]
    if not payloads:
        raise ValueError(f"岗位快照目录为空: {source_dir}")
    if not all(isinstance(item, dict) for item in payloads):
        raise ValueError("岗位快照文件根节点必须是对象")
    return refresh_snapshots(db, payloads)


def refresh_from_feed(db: Session, url: str, token: str = "") -> dict[str, int]:
    """从显式配置的可信采集器 feed 拉取 JSON；应用启动不会自动联网。"""
    if not url.startswith(("http://", "https://")):
        raise ValueError("岗位 feed URL 必须使用 http/https")
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.get(url, headers=headers, timeout=settings.job_market_timeout_seconds)
    response.raise_for_status()
    raw = response.json()
    payloads = raw.get("snapshots") if isinstance(raw, dict) and "snapshots" in raw else raw
    if isinstance(payloads, dict):
        payloads = [payloads]
    if not isinstance(payloads, list) or not all(isinstance(item, dict) for item in payloads):
        raise ValueError("岗位 feed 必须返回快照对象、对象数组或 {snapshots: [...]} ")
    return refresh_snapshots(db, payloads)


def _is_fresh(payload: dict[str, Any]) -> bool:
    try:
        fetched_at = _parse_time(payload.get("fetchedAt"))
    except ValueError:
        return False
    max_age = timedelta(hours=max(0.0, settings.job_market_max_age_hours))
    age = datetime.now(timezone.utc) - fetched_at
    return -timedelta(minutes=5) <= age <= max_age


# KP → 岗位雷达能力维（与 planner_agent._KP_TO_ABILITY 同口径）：掌握度并入对标
_KP_TO_ABILITY: dict[str, str] = {
    "ml": "机器学习基础",
    "nn": "神经网络",
    "dl": "深度学习",
    "cnn": "深度学习",
    "transformer": "Transformer",
    "finetune": "大模型微调",
}
# 已掌握某 KP → 对应能力维至少达到此水平（让"掌握度"体现在岗位匹配上）
_MASTERED_FLOOR = 82

# 目标岗位推断同义词（接口文档 5.3，C-fix 批2）：对话诊断仅得"学习目标"自由文本时，
# 映射到现有静态岗位库（不联网采集）。键 = JobSnapshot.id。
_JOB_KEYWORDS: dict[str, tuple[str, ...]] = {
    "llm-app": ("大模型", "llm", "应用", "prompt", "rag", "agent", "gpt", "aigc", "生成式"),
    "algo-engineer": ("算法",),
    "ml-engineer": ("机器学习", "建模", "训练"),
    "data-analyst": ("数据分析", "数据", "分析", "bi", "报表", "可视化"),
}


def hot_jobs(db: Session) -> list[dict[str, str]]:
    """热门岗位列表（接口文档 5.1）。按种子 sort_order（契约示例序）排序。"""
    rows = (
        db.query(JobSnapshot)
        .order_by(JobSnapshot.sort_order, JobSnapshot.id)
        .all()
    )
    return [{"id": row.id, "name": row.name} for row in rows]


def get_snapshot(db: Session, job_id: str) -> tuple[dict[str, Any], bool]:
    """岗位市场快照（接口文档 5.2）。返回 (payload, offline)。

    - 数据源正常：返回 (JobMarket, False)，路由套 code 0；
    - 数据源不可用（job_market_offline=True 模拟）：返回最近快照
      (JobMarket + offline:true, True)，路由套 code 2002（降级而非 500）；
    - 岗位不存在（含降级时无任何快照可回退）：抛 UnknownJob → 1004。
    """
    row = db.get(JobSnapshot, job_id)
    if row is None:
        raise UnknownJob(job_id)
    payload = dict(row.payload)
    if settings.job_market_offline or not _is_fresh(payload):
        # 最近快照 + 离线标记（payload.fetchedAt 不变，前端 timeAgo 显示快照年龄）
        payload["offline"] = True
        return payload, True
    return payload, False


def _main() -> None:
    parser = argparse.ArgumentParser(description="刷新岗位市场可信快照")
    parser.add_argument("--directory", default="")
    parser.add_argument("--url", default="")
    args = parser.parse_args()
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        if args.url or settings.job_market_feed_url:
            result = refresh_from_feed(
                db, args.url or settings.job_market_feed_url, settings.job_market_feed_token
            )
        else:
            result = refresh_from_directory(db, args.directory or settings.job_market_dir)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        db.close()


def _user_ability(db: Session, user_id: str) -> dict[str, int]:
    """用户 6 维能力（画像 + 掌握度）：ability-portrait 基线，已掌握 KP 抬升对应维。

    复用既有 profile.ability_portrait（4.4 雷达，最近 parse 优先、否则基线）+ Mastery
    （已 passed → 对应能力维并入 _MASTERED_FLOOR），体现"画像维度/掌握度"参与对标。
    """
    user = db.get(User, user_id)
    values = (
        list(profile_service.ability_portrait(user)["values"])
        if user is not None
        else list(_MOCK_BASELINE)
    )
    levels = {dim: int(v) for dim, v in zip(ABILITY_DIMENSIONS, values)}
    for kp_id, status in mastery_service.get_status_map(db, user_id).items():
        if status == mastery_service.STATUS_PASSED:
            dim = _KP_TO_ABILITY.get(kp_id)
            if dim:
                levels[dim] = max(levels.get(dim, 0), _MASTERED_FLOOR)
    return levels


def _resolve_job(
    db: Session, job_id: str | None, goal: str | None
) -> JobSnapshot | None:
    """解析目标岗位：优先 job_id；否则按学习目标文本关键词匹配现有静态岗位库。"""
    if job_id:
        snap = db.get(JobSnapshot, job_id)
        if snap is not None:
            return snap
    text = (goal or "").lower()
    if not text:
        return None
    best: JobSnapshot | None = None
    best_score = 0
    for snap in db.query(JobSnapshot).order_by(JobSnapshot.sort_order, JobSnapshot.id).all():
        score = 0
        name = snap.name or ""
        core = name.replace("工程师", "").replace("分析师", "")
        for kw in (name.lower(), core.lower()):
            if len(kw) >= 2 and kw in text:
                score += 2
        for kw in _JOB_KEYWORDS.get(snap.id, ()):  # noqa: SIM110
            if kw in text:
                score += 1
        if score > best_score:
            best_score, best = score, snap
    return best if best_score > 0 else None


def match_job(
    db: Session,
    user_id: str,
    *,
    job_id: str | None = None,
    goal: str | None = None,
) -> dict[str, Any]:
    """岗位匹配度 + 能力缺口（接口文档 5.3，C-fix 批2）。

    用现有静态岗位库（JobSnapshot.radar 6 维需求）与用户能力（画像 + 掌握度）对标：
    - matchPct = 达标维度占比 × 100（达标 = 用户该维 ≥ 岗位需求），与前端岗位对标同口径；
    - gaps = 未达标维度 [{dimension, level, demand, gap}]，按缺口降序；
    - radar = {dimensions, values(用户), demand(岗位)} 供雷达对比渲染。
    岗位无法解析（job_id 不存在且 goal 未命中任何岗位）→ 抛 UnknownJob（→ 1004）。
    **仅用现有静态数据，不触发任何联网采集。**
    """
    snap = _resolve_job(db, job_id, goal)
    if snap is None:
        raise UnknownJob(job_id or goal or "")

    radar = {
        k: int(v)
        for k, v in ((snap.payload or {}).get("radar") or {}).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    levels = _user_ability(db, user_id)

    values: list[int] = []
    demand: list[int] = []
    gaps: list[dict[str, Any]] = []
    met = 0
    for dim in ABILITY_DIMENSIONS:
        lvl = int(levels.get(dim, 0))
        dem = int(radar.get(dim, 0))
        values.append(lvl)
        demand.append(dem)
        if lvl >= dem:
            met += 1
        else:
            gaps.append({"dimension": dim, "level": lvl, "demand": dem, "gap": dem - lvl})
    gaps.sort(key=lambda g: g["gap"], reverse=True)
    match_pct = round(met / len(ABILITY_DIMENSIONS) * 100) if ABILITY_DIMENSIONS else 0

    return {
        "jobId": snap.id,
        "jobName": snap.name,
        "matchPct": match_pct,
        "gaps": gaps,
        "radar": {"dimensions": list(ABILITY_DIMENSIONS), "values": values, "demand": demand},
    }


if __name__ == "__main__":
    _main()
