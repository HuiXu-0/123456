"""模型管理契约测试（接口文档 21，additive）。

覆盖：
- 注册表接口：mock 模式仅内置 mock；deepseek 模式 = 默认 DeepSeek + 魔搭清单；
- 切换：PUT /models/current 生效、未知 id → 1001/400、默认行为不变（current=deepseek）；
- 分发通道 llm_transport：当前模型为魔搭 → 走 llm_modelscope；失败 → 优雅回落
  默认 DeepSeek（绝不崩）；默认模型 → 与旧行为逐字一致（直走 llm_deepseek）；
- GEN 板块论文级图解：13 个 GEN-* 模板全部合法可渲染，且真实模式亦模板优先（零网络）。

全程 mock/monkeypatch，零网络（CLAUDE.md 纪律）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import llm_deepseek, llm_modelscope, llm_transport, model_registry
from app.core.config import settings
from app.core.llm import LLMClient, LLMGenerationError
from app.core import llm as llm_mod
from app.main import app

API = "/api/v1"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def learner(client) -> dict[str, str]:
    res = client.post(f"{API}/auth/login", json={"username": "learner_001", "password": "123456"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture()
def deepseek_mode(monkeypatch):
    """临时切到 deepseek provider（conftest 基线为 mock），并隔离当前模型状态。"""
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    model_registry.reset()
    yield
    model_registry.reset()


def _data(res, *, code: int = 0, status: int = 200):
    assert res.status_code == status, f"HTTP {res.status_code}: {res.text[:300]}"
    body = res.json()
    assert body["code"] == code, f"code {body['code']} != {code}: {body['message']}"
    return body["data"]


# ---------------------------------------------------------------------------- #
# 注册表接口
# ---------------------------------------------------------------------------- #
def test_models_requires_auth(client):
    assert client.get(f"{API}/models").status_code in (401, 403)


def test_mock_mode_registry(client, learner):
    """mock 模式：注册表仅内置 mock（无 Key 全链路可跑的纪律不变）。"""
    data = _data(client.get(f"{API}/models", headers=learner))
    assert data["current"] == "mock"
    assert [m["id"] for m in data["models"]] == ["mock"]
    assert data["models"][0]["available"] is True


def test_deepseek_mode_registry_default_unchanged(client, learner, deepseek_mode):
    """deepseek 模式：默认当前模型 = 既有 DeepSeek（不切换则行为不变），魔搭清单在列。"""
    data = _data(client.get(f"{API}/models", headers=learner))
    assert data["current"] == settings.deepseek_model
    ids = [m["id"] for m in data["models"]]
    assert ids[0] == settings.deepseek_model
    for mid in ("ZhipuAI/GLM-4.6", "Qwen/Qwen3-32B", "deepseek-ai/DeepSeek-V3.1"):
        assert mid in ids
    ms = next(m for m in data["models"] if m["provider"] == "modelscope")
    assert ms["available"] is False  # 未配 MODELSCOPE_API_KEY → 标记不可直连（仍可选）


def test_switch_model_and_reject_unknown(client, learner, deepseek_mode):
    data = _data(
        client.put(f"{API}/models/current", headers=learner, json={"modelId": "ZhipuAI/GLM-4.6"})
    )
    assert data["current"] == "ZhipuAI/GLM-4.6"
    assert model_registry.current().provider == "modelscope"
    # 未知模型 → 1001/400，当前模型不变
    _data(
        client.put(f"{API}/models/current", headers=learner, json={"modelId": "no/such-model"}),
        code=1001,
        status=400,
    )
    assert model_registry.current().id == "ZhipuAI/GLM-4.6"


# ---------------------------------------------------------------------------- #
# 分发通道：走当前模型 + 优雅降级
# ---------------------------------------------------------------------------- #
def test_transport_default_goes_deepseek(monkeypatch, deepseek_mode):
    """默认模型（未切换）：与旧行为逐字一致，直走 llm_deepseek，不碰魔搭。"""
    monkeypatch.setattr(llm_deepseek, "chat", lambda p, system=None, history=None: "来自DeepSeek")
    monkeypatch.setattr(
        llm_modelscope, "chat",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("默认模型不应触达魔搭")),
    )
    assert llm_transport.chat("你好") == "来自DeepSeek"


def test_transport_dispatch_modelscope(monkeypatch, deepseek_mode):
    """切到魔搭模型后：生成走 llm_modelscope，且透传所选 model_id。"""
    model_registry.set_current("Qwen/Qwen3-32B")
    seen: dict = {}

    def fake_chat(prompt, system=None, history=None, *, model):
        seen["model"] = model
        return "来自魔搭"

    monkeypatch.setattr(llm_modelscope, "chat", fake_chat)
    assert llm_transport.chat("你好") == "来自魔搭"
    assert seen["model"] == "Qwen/Qwen3-32B"


def test_transport_fallback_on_modelscope_failure(monkeypatch, deepseek_mode):
    """魔搭调用失败/未配 Key → 优雅回落默认 DeepSeek（绝不崩）。"""
    model_registry.set_current("ZhipuAI/GLM-4.6")
    monkeypatch.setattr(
        llm_modelscope, "chat",
        lambda *a, **k: (_ for _ in ()).throw(LLMGenerationError("MODELSCOPE_API_KEY 未配置")),
    )
    monkeypatch.setattr(llm_deepseek, "chat", lambda p, system=None, history=None: "回落DeepSeek")
    assert llm_transport.chat("你好") == "回落DeepSeek"


def test_transport_stream_fallback(monkeypatch, deepseek_mode):
    """魔搭流式开流即失败（未产出内容）→ 回落默认 DeepSeek 流。"""
    model_registry.set_current("ZhipuAI/GLM-4.6")

    def broken_stream(*a, **k):
        raise LLMGenerationError("魔搭流式调用失败")
        yield  # pragma: no cover

    monkeypatch.setattr(llm_modelscope, "chat_stream", broken_stream)
    monkeypatch.setattr(
        llm_deepseek, "chat_stream",
        lambda p, system=None, history=None: iter(["回", "落"]),
    )
    assert "".join(llm_transport.chat_stream("你好")) == "回落"


# ---------------------------------------------------------------------------- #
# GEN 板块论文级图解（Diffusion 配图）
# ---------------------------------------------------------------------------- #
def test_gen_diagram_templates_complete_and_valid():
    """GEN-1..GEN-13 模板齐全，且首行均为受支持 Mermaid 图型（可渲染契约）。"""
    for i in range(1, 14):
        kp_id = f"GEN-{i}"
        tpl = llm_mod._DIAGRAM_TEMPLATES.get(kp_id)
        assert tpl, f"缺少 {kp_id} 论文级图解模板"
        assert llm_mod._clean_mermaid(tpl)  # 非法首行会抛 LLMGenerationError


def test_gen_diagram_template_first_even_in_real_mode(deepseek_mode):
    """真实模式下 GEN 板块模板优先：零网络返回精写论文级图解（质量确定性达标）。"""
    out = LLMClient("deepseek").generate_diagram("GEN-9", "文生图实践（Stable Diffusion）")
    assert out["mermaid"] == llm_mod._DIAGRAM_TEMPLATES["GEN-9"]
    out_mock = LLMClient("mock").generate_diagram("GEN-4", "扩散模型原理（DDPM）")
    assert out_mock["mermaid"] == llm_mod._DIAGRAM_TEMPLATES["GEN-4"]
