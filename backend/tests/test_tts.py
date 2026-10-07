"""语音合成 TTS 契约 + 缓存 + 降级测试（接口文档 8.9，新增接口 45）。

不依赖真实联网：用 monkeypatch 替换 edge-tts 单次合成函数，验证
- 成功返回 audio/mpeg 流；
- 同文本二次调用命中缓存（X-TTS-Cache: hit，且不再触发合成）；
- tts_provider=none / 合成多次失败 → 优雅降级 2002（HTTP 200, offline=true）；
- 需登录、空文本 → 1001。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import tts as tts_service

FAKE_MP3 = b"ID3FAKEMP3PAYLOAD-zh-CN-XiaoyiNeural"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def headers(client) -> dict[str, str]:
    res = client.post("/api/v1/auth/login", json={"username": "learner_001", "password": "123456"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture()
def tts_cache(tmp_path, monkeypatch):
    """每个用例独立的缓存目录 + 默认 edge provider，避免相互污染。"""
    monkeypatch.setattr(settings, "tts_cache_dir", str(tmp_path / "tts_cache"))
    monkeypatch.setattr(settings, "tts_provider", "edge")
    monkeypatch.setattr(settings, "tts_max_retries", 3)
    return tmp_path


@pytest.fixture()
def fake_edge(monkeypatch):
    """替换 edge-tts 单次合成：记录调用次数，返回固定 MP3 字节（不联网）。"""
    calls = {"n": 0}

    async def _fake_once(text, voice, rate, pitch):  # noqa: ANN001
        calls["n"] += 1
        return FAKE_MP3

    monkeypatch.setattr(tts_service, "_edge_once", _fake_once)
    return calls


def test_requires_login(client):
    assert client.post("/api/v1/tts/synthesize", json={"text": "你好"}).status_code == 401


def test_empty_text_returns_1001(client, headers, tts_cache):
    res = client.post("/api/v1/tts/synthesize", json={"text": "   "}, headers=headers)
    assert res.status_code == 400
    assert res.json()["code"] == 1001


def test_synthesize_returns_mp3_stream(client, headers, tts_cache, fake_edge):
    res = client.post(
        "/api/v1/tts/synthesize", json={"text": "勾股定理是直角三角形的基本定理。"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.headers["x-tts-cache"] == "miss"
    assert res.content == FAKE_MP3
    assert fake_edge["n"] == 1


def test_second_call_hits_cache(client, headers, tts_cache, fake_edge):
    payload = {"text": "缓存命中应当更快且不再合成。"}
    first = client.post("/api/v1/tts/synthesize", json=payload, headers=headers)
    assert first.headers["x-tts-cache"] == "miss"
    second = client.post("/api/v1/tts/synthesize", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.headers["x-tts-cache"] == "hit"
    assert second.content == FAKE_MP3
    # 第二次走缓存：底层合成仅被调用过一次
    assert fake_edge["n"] == 1


def test_provider_none_degrades(client, headers, tts_cache, monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "none")
    res = client.post("/api/v1/tts/synthesize", json={"text": "禁用时应降级"}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 2002
    assert body["data"]["offline"] is True


def test_edge_failure_degrades_after_retries(client, headers, tts_cache, monkeypatch):
    calls = {"n": 0}

    async def _boom(text, voice, rate, pitch):  # noqa: ANN001
        calls["n"] += 1
        raise RuntimeError("模拟断网")

    monkeypatch.setattr(tts_service, "_edge_once", _boom)
    res = client.post("/api/v1/tts/synthesize", json={"text": "断网降级"}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 2002
    assert body["data"]["offline"] is True
    # 重试了配置的次数后才放弃
    assert calls["n"] == settings.tts_max_retries


def test_service_cache_key_stable(tts_cache, fake_edge):
    """同参 → 同缓存键命中；不同 voice → 不同键、重新合成。"""

    async def _run():
        a1, c1 = await tts_service.synthesize("文本", voice="v1", rate="-10%", pitch="+2Hz")
        a2, c2 = await tts_service.synthesize("文本", voice="v1", rate="-10%", pitch="+2Hz")
        a3, c3 = await tts_service.synthesize("文本", voice="v2", rate="-10%", pitch="+2Hz")
        return (c1, c2, c3)

    c1, c2, c3 = asyncio.run(_run())
    assert c1 is False and c2 is True and c3 is False
    assert fake_edge["n"] == 2  # v1 合成一次（第二次命中缓存），v2 合成一次
