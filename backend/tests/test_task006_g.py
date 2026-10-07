"""TASK-006-G：岗位刷新、真实资源缓存、数据驱动图谱。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import ExternalResourceCache, JobSnapshot, Mastery, User
from app.services import job_market, knowledge_graph, resource_search, web_search


def _snapshot(job_id: str, fetched_at: datetime) -> dict:
    return {
        "id": job_id,
        "name": "测试真实岗位",
        "salaryRange": "20K–35K · 13薪",
        "salaryMedian": "28K",
        "heat": "高",
        "heatPct": 80,
        "openings": 123,
        "source": "测试采集器公开 JD 样本",
        "fetchedAt": fetched_at.isoformat(),
        "skills": [{"name": "Python", "freqPct": 85}],
        "radar": {dimension: 60 for dimension in job_market.ABILITY_DIMENSIONS},
    }


def test_job_snapshot_refresh_validates_newer_data_and_staleness(monkeypatch):
    job_id = "g-real-job"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.query(JobSnapshot).filter(JobSnapshot.id == job_id).delete()
        db.commit()
        result = job_market.refresh_snapshots(db, [_snapshot(job_id, now)])
        assert result == {"updated": 1, "skipped": 0, "total": 1}
        assert job_market.refresh_snapshots(
            db, [_snapshot(job_id, now - timedelta(hours=1))]
        ) == {"updated": 0, "skipped": 1, "total": 1}

        monkeypatch.setattr(settings, "job_market_max_age_hours", 12.0)
        payload, offline = job_market.get_snapshot(db, job_id)
        assert offline is False
        assert payload["source"] == "测试采集器公开 JD 样本"

        stale = _snapshot(job_id, now - timedelta(days=2))
        stale["id"] = "g-stale-job"
        job_market.refresh_snapshots(db, [stale])
        stale_payload, stale_offline = job_market.get_snapshot(db, "g-stale-job")
        assert stale_offline is True
        assert stale_payload["offline"] is True
    finally:
        db.query(JobSnapshot).filter(JobSnapshot.id.in_([job_id, "g-stale-job"])).delete(
            synchronize_session=False
        )
        db.commit()
        db.close()


def test_job_snapshot_validation_is_atomic():
    db = SessionLocal()
    try:
        good = _snapshot("g-atomic-good", datetime.now(timezone.utc))
        bad = dict(good)
        bad["id"] = "g-atomic-bad"
        bad["radar"] = {"机器学习基础": 50}
        try:
            job_market.refresh_snapshots(db, [good, bad])
        except ValueError as exc:
            assert "radar" in str(exc)
        else:
            raise AssertionError("非法快照必须阻止整批写入")
        assert db.get(JobSnapshot, "g-atomic-good") is None
    finally:
        db.query(JobSnapshot).filter(JobSnapshot.id.like("g-atomic-%")).delete(
            synchronize_session=False
        )
        db.commit()
        db.close()


def test_external_search_uses_fresh_and_stale_real_cache(monkeypatch):
    class Provider:
        name = "test-live"
        online = True

        def __init__(self):
            self.calls = 0
            self.available = True

        def search(self, query, *, max_results):
            self.calls += 1
            if not self.available:
                return []
            return [{
                "title": "真实检索文档",
                "url": "https://example.edu/real",
                "source": "example.edu",
                "snippet": "神经网络反向传播教程",
                "type": "文档",
            }]

    provider = Provider()
    monkeypatch.setattr(web_search, "get_provider", lambda: provider)
    db = SessionLocal()
    try:
        db.query(ExternalResourceCache).filter(ExternalResourceCache.kp_id == "nn").delete()
        db.commit()
        first = resource_search.aggregate(db, "u_10001", "nn", query="反向传播")
        second = resource_search.aggregate(db, "u_10001", "nn", query="反向传播")
        assert first["online"] is True and second["online"] is True
        assert provider.calls == 1
        assert second["items"] == first["items"]

        row = db.query(ExternalResourceCache).filter(ExternalResourceCache.kp_id == "nn").one()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
        provider.available = False
        degraded = resource_search.aggregate(db, "u_10001", "nn", query="反向传播")
        assert provider.calls == 2
        assert degraded["online"] is False
        assert degraded["items"] == first["items"]
    finally:
        db.query(ExternalResourceCache).filter(ExternalResourceCache.kp_id == "nn").delete()
        db.commit()
        db.close()


def test_graph_topology_and_extension_node_use_database_state():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "learner_001").one()
        original = db.query(Mastery).filter(
            Mastery.user_id == user.id, Mastery.kp_id == "AGT-1"
        ).one_or_none()
        saved = None
        if original is not None:
            saved = (original.status, original.score, original.confidence, original.score_source)
            db.delete(original)
            db.commit()

        graph = knowledge_graph.get_graph(db, user.id)
        assert len(graph["nodes"]) == 12
        assert len(graph["links"]) == 14
        assert {"source": "prompt", "target": "agent"} in graph["links"]

        db.add(Mastery(
            user_id=user.id,
            kp_id="AGT-1",
            status="learning",
            score=15,
            confidence=0.8,
            score_source="quiz",
        ))
        db.commit()
        agent = next(node for node in knowledge_graph.derived_nodes(db, user.id) if node["id"] == "agent")
        assert agent["category"] == 3
        assert agent["value"] == 15
    finally:
        db.query(Mastery).filter(
            Mastery.user_id == user.id, Mastery.kp_id == "AGT-1"
        ).delete()
        if saved is not None:
            db.add(Mastery(
                user_id=user.id,
                kp_id="AGT-1",
                status=saved[0],
                score=saved[1],
                confidence=saved[2],
                score_source=saved[3],
            ))
        db.commit()
        db.close()
