"""外部资源·联网搜索聚合 契约测试（接口文档 8.6 增量，C-fix 批3-bonus）。

- POST /resource/external/aggregate：聚合 Agent 整理 + critic 评分，返回带相关度/可信度清单。
- 无搜索能力（默认 search_provider=none）→ online=false + 种子兜底候选，**保证可跑**。
- 需登录；知识点不存在 → 1004；既有 GET /resource/external/{kpId} 回归未变。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

AGG_KEYS = {"kpId", "kpName", "provider", "online", "items", "generationMeta"}
ITEM_KEYS = {"id", "type", "title", "source", "url", "relevance", "credibility", "reason"}
RES_TYPES = {"视频", "论文", "文档", "课程"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def headers(client) -> dict[str, str]:
    res = client.post("/api/v1/auth/login", json={"username": "learner_001", "password": "123456"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


def _data(res) -> dict:
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0, body
    return body["data"]


def test_aggregate_requires_login(client):
    assert client.post("/api/v1/resource/external/aggregate", json={"kpId": "nn"}).status_code == 401


def test_aggregate_unknown_kp(client, headers):
    res = client.post("/api/v1/resource/external/aggregate", headers=headers, json={"kpId": "zzz"})
    assert res.status_code == 404 and res.json()["code"] == 1004


def test_aggregate_offline_seed_fallback(client, headers):
    """默认无搜索能力 → online=false + 种子兜底候选，聚合评分后返回合规清单。"""
    data = _data(client.post("/api/v1/resource/external/aggregate", headers=headers, json={"kpId": "nn"}))
    assert set(data) == AGG_KEYS
    assert data["kpId"] == "nn"
    assert data["provider"] == "none"  # 未配置搜索 API
    assert data["online"] is False
    items = data["items"]
    assert isinstance(items, list) and items
    rels = []
    for it in items:
        assert ITEM_KEYS <= set(it)  # 允许附加 embed/duration
        assert it["type"] in RES_TYPES
        assert 0 <= it["relevance"] <= 100 and 0 <= it["credibility"] <= 100
        assert it["url"].startswith("http")
        assert it["reason"]
        rels.append(it["relevance"])
    assert rels == sorted(rels, reverse=True)  # 按相关度降序


def test_aggregate_custom_weakpoints(client, headers):
    """显式薄弱点参与聚合（因人而异），仍返回合规清单。"""
    data = _data(client.post(
        "/api/v1/resource/external/aggregate",
        headers=headers,
        json={"kpId": "nn", "weakPoints": ["反向传播", "梯度下降"]},
    ))
    assert data["items"] and all(it["url"].startswith("http") for it in data["items"])


def _agg_stub(i: int, type_: str, rel: int) -> dict:
    return {
        "id": f"agg-{i}",
        "type": type_,
        "title": f"候选{i}",
        "source": "web",
        "url": f"https://example.com/{i}",
        "relevance": rel,
        "credibility": 90,
        "reason": "测试",
    }


def test_video_guarantee_swap():
    """Top-N 无视频且候选池有 → 以最高分视频替换最低分非视频项（总数不变、仍降序）。"""
    from app.core.llm import LLMClient

    ranked = [_agg_stub(i, "文档", 99 - i) for i in range(8)]
    ranked += [_agg_stub(9, "视频", 80), _agg_stub(10, "视频", 70)]
    top = LLMClient._ensure_video(ranked)
    assert len(top) == 8
    videos = [it for it in top if it["type"] == "视频"]
    assert videos and videos[0]["relevance"] == 80  # 取候选池评分最高的视频
    rels = [it["relevance"] for it in top]
    assert rels == sorted(rels, reverse=True)  # 替换后仍按相关度降序


def test_video_guarantee_already_present():
    """Top-N 已含视频 → 原样返回，不做替换。"""
    from app.core.llm import LLMClient

    ranked = [_agg_stub(1, "视频", 95)] + [_agg_stub(i, "文档", 90 - i) for i in range(2, 10)]
    top = LLMClient._ensure_video(ranked)
    assert top == ranked[:8]


def test_video_guarantee_degrade_without_video():
    """候选池确实无视频 → 不硬塞，正常返回 Top-N（可解释降级，日志说明）。"""
    from app.core.llm import LLMClient

    ranked = [_agg_stub(i, "论文", 99 - i) for i in range(9)]
    top = LLMClient._ensure_video(ranked)
    assert len(top) == 8 and all(it["type"] != "视频" for it in top)


def test_aggregate_offline_contains_video(client, headers):
    """种子兜底聚合 → 最终清单至少 1 条视频（形态保底，nn/cnn 均成立）。"""
    for kp_id in ("nn", "cnn"):
        data = _data(client.post("/api/v1/resource/external/aggregate", headers=headers, json={"kpId": kp_id}))
        assert any(it["type"] == "视频" for it in data["items"]), kp_id


def test_aggregate_catalog_kp_generic_seed_fallback(client, headers):
    """体系拓展点（无专属种子，如 AGT-1）→ 全库精选兜底：推荐非空且含视频。"""
    data = _data(client.post("/api/v1/resource/external/aggregate", headers=headers, json={"kpId": "AGT-1"}))
    items = data["items"]
    assert items, "无专属种子的 kp 也应有推荐（全库精选兜底）"
    assert any(it["type"] == "视频" for it in items)
    rels = [it["relevance"] for it in items]
    assert rels == sorted(rels, reverse=True)


def test_existing_external_endpoint_unchanged(client, headers):
    """既有 GET /resource/external/{kpId}（静态种子）回归未变。"""
    data = _data(client.get("/api/v1/resource/external/nn", headers=headers))
    assert isinstance(data, list) and data
    assert {"id", "type", "title", "source", "url", "relevance", "credibility", "reason"} <= set(data[0])
