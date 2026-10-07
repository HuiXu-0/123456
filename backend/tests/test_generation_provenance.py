"""TASK-006-C：生成结果运行时溯源元数据契约。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core import generation_provenance, llm_transport, model_registry
from app.core.llm_deepseek import LLMGenerationError
from app.main import app


META_KEYS = {"provider", "model", "source", "degraded", "fallbackReason"}


def _headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": "learner_001", "password": "123456"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['token']}"}


def _done_payload(text: str) -> dict:
    block = next(part for part in text.split("\n\n") if "event: done" in part)
    line = next(line for line in block.splitlines() if line.startswith("data: "))
    return json.loads(line.removeprefix("data: "))


def test_structured_generation_result_has_exact_mock_metadata():
    with TestClient(app) as client:
        response = client.get("/api/v1/resource/mindmap/nn", headers=_headers(client))
    assert response.status_code == 200, response.text
    meta = response.json()["data"]["generationMeta"]
    assert set(meta) == META_KEYS
    assert meta == {
        "provider": "internal",
        "model": "deterministic",
        "source": "deterministic",
        "degraded": False,
        "fallbackReason": None,
    }


def test_sse_done_event_has_same_metadata_contract():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/resource/tutor/chat",
            headers={**_headers(client), "Accept": "text/event-stream"},
            json={"kpId": "nn", "message": "激活函数有什么作用？"},
        )
    assert response.status_code == 200, response.text
    meta = _done_payload(response.text)["generationMeta"]
    assert set(meta) == META_KEYS
    assert meta["source"] == "mock"
    assert meta["degraded"] is False


def test_provider_failure_records_actual_deepseek_fallback(monkeypatch):
    custom = model_registry.ModelSpec(
        id="umc_test",
        label="test",
        provider="openai-compatible",
        base_url="https://invalid.example/v1",
        model_id="custom-model",
        source="custom",
        api_key="secret",
    )
    monkeypatch.setattr(model_registry, "current", lambda: custom)
    monkeypatch.setattr(
        llm_transport.llm_userconf,
        "chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(LLMGenerationError("masked")),
    )
    monkeypatch.setattr(llm_transport.llm_deepseek, "chat", lambda *args, **kwargs: "ok")

    with generation_provenance.bind_trace():
        assert llm_transport.chat("hello") == "ok"
        meta = generation_provenance.current_trace().as_dict()
    assert meta["provider"] == "deepseek"
    assert meta["source"] == "fallback"
    assert meta["degraded"] is True
    assert meta["fallbackReason"] == "provider_unavailable"


def test_cache_and_deterministic_fallback_are_distinguishable():
    with generation_provenance.bind_trace():
        generation_provenance.mark_cache()
        cached = generation_provenance.current_trace().as_dict()
    with generation_provenance.bind_trace():
        generation_provenance.mark_degraded()
        fallback = generation_provenance.current_trace().as_dict()
    assert cached["source"] == "cache" and cached["degraded"] is False
    assert fallback["source"] == "fallback" and fallback["degraded"] is True
    assert fallback["provider"] == "internal" and fallback["model"] == "deterministic"
    assert fallback["fallbackReason"] == "deterministic_fallback"


def test_list_results_attach_metadata_to_each_generated_item():
    with generation_provenance.bind_trace():
        result = generation_provenance.attach_generation_meta([{"id": "a"}, {"id": "b"}])
    assert [item["id"] for item in result] == ["a", "b"]
    assert all(set(item["generationMeta"]) == META_KEYS for item in result)
