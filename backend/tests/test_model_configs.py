"""用户自建模型配置契约测试（接口文档 21.3+，模型管理独立页）。

覆盖（对应 CC-model-management-page.md 验证项）：
- CRUD：新增/编辑/删除自建配置；GET /models 列出（source=custom）；
- key 安全：响应只回脱敏形（****后四位）、DB 落库为 Fernet 密文非明文、
  日志过滤器兜底掩码、redact 清洗异常串；
- 按 user 隔离：B 看不到/改不动/删不掉 A 的配置（1004），注册表互不串；
- per-user 当前模型：A 切自建仅 A 生效（overlay），B 与无上下文默认不变；
  切回内置清 overlay；删除当前配置自动回落默认；
- 分发与降级：bind_user 后 llm_transport 走 llm_userconf 并透传配置；
  自建失败优雅回落默认 DeepSeek（绝不崩）；
- 测试连通性：POST /models/configs/test 表单值/configId 两形态与参数校验。

全程 mock/monkeypatch，零网络（CLAUDE.md 纪律）。用例自清理（不污染
test_model_registry 的 mock 模式断言）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import crypto, llm_deepseek, llm_transport, llm_userconf, model_registry
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm import LLMGenerationError
from app.core.logging import mask_pii
from app.main import app
from app.models.entities import UserModelChoice, UserModelConfig

API = "/api/v1"
PLAIN_KEY = "ms-0123456789abcdef-secret-demo"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, username: str, password: str = "123456") -> dict[str, str]:
    res = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture(scope="module")
def learner(client) -> dict[str, str]:
    return _login(client, "learner_001")


@pytest.fixture(scope="module")
def other_user(client) -> dict[str, str]:
    """第二个用户（隔离用例）：注册一次性账号。"""
    client.post(
        f"{API}/auth/register",
        json={"username": "mmc_user_b", "password": "123456"},
    )
    return _login(client, "mmc_user_b")


@pytest.fixture()
def deepseek_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    model_registry.reset()
    yield
    model_registry.reset()


def _data(res, *, code: int = 0, status: int = 200):
    assert res.status_code == status, f"HTTP {res.status_code}: {res.text[:300]}"
    body = res.json()
    assert body["code"] == code, f"code {body['code']} != {code}: {body['message']}"
    return body["data"]


def _create(client, headers, **overrides) -> dict:
    payload = {
        "label": "我的GLM",
        "provider": "modelscope",
        "baseUrl": "https://api-inference.modelscope.cn/v1",
        "modelId": "ZhipuAI/GLM-4.6",
        "apiKey": PLAIN_KEY,
    }
    payload.update(overrides)
    return _data(client.post(f"{API}/models/configs", headers=headers, json=payload))


def _delete(client, headers, config_id: str) -> None:
    client.delete(f"{API}/models/configs/{config_id}", headers=headers)


# ---------------------------------------------------------------------------- #
# CRUD + key 脱敏/加密
# ---------------------------------------------------------------------------- #
def test_create_returns_masked_key_and_encrypts_at_rest(client, learner):
    cfg = _create(client, learner)
    try:
        assert cfg["id"].startswith("umc_")
        assert cfg["apiKeyMasked"] == f"****{PLAIN_KEY[-4:]}"
        # 响应任何位置不含明文 key
        assert PLAIN_KEY not in str(cfg)
        # DB 落库为密文：非明文、不含明文子串，且可解回明文
        with SessionLocal() as db:
            row = db.get(UserModelConfig, cfg["id"])
            assert row is not None
            assert row.api_key_encrypted != PLAIN_KEY
            assert PLAIN_KEY not in row.api_key_encrypted
            assert crypto.decrypt_key(row.api_key_encrypted) == PLAIN_KEY
    finally:
        _delete(client, learner, cfg["id"])


def test_registry_lists_custom_with_source_and_mask(client, learner):
    cfg = _create(client, learner)
    try:
        data = _data(client.get(f"{API}/models", headers=learner))
        mine = next(m for m in data["models"] if m["id"] == cfg["id"])
        assert mine["source"] == "custom"
        assert mine["provider"] == "modelscope"
        assert mine["available"] is True
        assert mine["apiKeyMasked"] == f"****{PLAIN_KEY[-4:]}"
        assert PLAIN_KEY not in str(data)
        # 内置条目仍在首位（mock 基线 → 首项 mock；契约字段 additive 不缺）
        assert data["models"][0]["source"] == "builtin"
    finally:
        _delete(client, learner, cfg["id"])


def test_update_keeps_key_when_blank_and_rotates_when_given(client, learner):
    cfg = _create(client, learner)
    try:
        # 不带 apiKey → 保留原 key
        updated = _data(
            client.put(
                f"{API}/models/configs/{cfg['id']}",
                headers=learner,
                json={
                    "label": "改名GLM",
                    "provider": "modelscope",
                    "baseUrl": "https://api-inference.modelscope.cn/v1",
                    "modelId": "ZhipuAI/GLM-4.6",
                },
            )
        )
        assert updated["label"] == "改名GLM"
        assert updated["apiKeyMasked"] == f"****{PLAIN_KEY[-4:]}"
        # 带新 key → 轮换
        updated = _data(
            client.put(
                f"{API}/models/configs/{cfg['id']}",
                headers=learner,
                json={
                    "label": "改名GLM",
                    "provider": "modelscope",
                    "baseUrl": "https://api-inference.modelscope.cn/v1",
                    "modelId": "ZhipuAI/GLM-4.6",
                    "apiKey": "sk-new-key-wxyz",
                },
            )
        )
        assert updated["apiKeyMasked"] == "****wxyz"
    finally:
        _delete(client, learner, cfg["id"])


def test_validation_rejects_bad_payload(client, learner):
    _data(
        client.post(
            f"{API}/models/configs",
            headers=learner,
            json={
                "label": "坏配置",
                "provider": "not-a-provider",
                "baseUrl": "https://x.example.com/v1",
                "modelId": "m",
                "apiKey": "k12345",
            },
        ),
        code=1001,
        status=400,
    )
    _data(
        client.post(
            f"{API}/models/configs",
            headers=learner,
            json={
                "label": "坏配置",
                "provider": "openai",
                "baseUrl": "ftp://x.example.com",
                "modelId": "m",
                "apiKey": "k12345",
            },
        ),
        code=1001,
        status=400,
    )


# ---------------------------------------------------------------------------- #
# 按 user 隔离
# ---------------------------------------------------------------------------- #
def test_user_isolation(client, learner, other_user):
    cfg = _create(client, learner)
    try:
        # B 的注册表不含 A 的配置
        data_b = _data(client.get(f"{API}/models", headers=other_user))
        assert all(m["id"] != cfg["id"] for m in data_b["models"])
        # B 改/删/切 A 的配置 → 1004 或 1001（不泄露存在性、不生效）
        _data(
            client.put(
                f"{API}/models/configs/{cfg['id']}",
                headers=other_user,
                json={
                    "label": "越权",
                    "provider": "modelscope",
                    "baseUrl": "https://api-inference.modelscope.cn/v1",
                    "modelId": "ZhipuAI/GLM-4.6",
                },
            ),
            code=1004,
            status=404,
        )
        _data(
            client.delete(f"{API}/models/configs/{cfg['id']}", headers=other_user),
            code=1004,
            status=404,
        )
        _data(
            client.put(f"{API}/models/current", headers=other_user, json={"modelId": cfg["id"]}),
            code=1001,
            status=400,
        )
        # B 用 configId 测试连通 A 的配置 → 1004
        _data(
            client.post(
                f"{API}/models/configs/test", headers=other_user, json={"configId": cfg["id"]}
            ),
            code=1004,
            status=404,
        )
    finally:
        _delete(client, learner, cfg["id"])


# ---------------------------------------------------------------------------- #
# per-user 当前模型 + 分发/降级
# ---------------------------------------------------------------------------- #
def test_per_user_current_and_fallback(client, learner, other_user, deepseek_mode, monkeypatch):
    cfg = _create(client, learner)
    try:
        data = _data(client.put(f"{API}/models/current", headers=learner, json={"modelId": cfg["id"]}))
        assert data["current"] == cfg["id"]
        # B 与无上下文视角：默认 DeepSeek 不变（A 的选择不外溢）
        data_b = _data(client.get(f"{API}/models", headers=other_user))
        assert data_b["current"] == settings.deepseek_model
        assert model_registry.current().id == settings.deepseek_model

        # 绑定 A 上下文 → current() 解析为自建配置（key 已解密，仅内存）
        token = model_registry.bind_user("u_10001")
        try:
            spec = model_registry.current()
            assert spec.source == "custom"
            assert spec.id == cfg["id"]
            assert spec.api_key == PLAIN_KEY

            # 分发：走 llm_userconf 并透传配置
            seen: dict = {}

            def fake_chat(prompt, system=None, history=None, *, spec):
                seen["model"] = spec.model_id
                seen["key"] = spec.api_key
                return "来自自建配置"

            monkeypatch.setattr(llm_userconf, "chat", fake_chat)
            assert llm_transport.chat("你好") == "来自自建配置"
            assert seen == {"model": "ZhipuAI/GLM-4.6", "key": PLAIN_KEY}

            # 降级：自建失败 → 回落默认 DeepSeek（绝不崩）
            monkeypatch.setattr(
                llm_userconf, "chat",
                lambda *a, **k: (_ for _ in ()).throw(LLMGenerationError("上游 401")),
            )
            monkeypatch.setattr(
                llm_deepseek, "chat", lambda p, system=None, history=None: "回落DeepSeek"
            )
            assert llm_transport.chat("你好") == "回落DeepSeek"
        finally:
            model_registry.unbind_user(token)

        # 切回内置 → 清 overlay
        data = _data(
            client.put(
                f"{API}/models/current", headers=learner, json={"modelId": settings.deepseek_model}
            )
        )
        assert data["current"] == settings.deepseek_model
        with SessionLocal() as db:
            assert db.get(UserModelChoice, "u_10001") is None
    finally:
        _delete(client, learner, cfg["id"])


def test_delete_current_config_falls_back_to_default(client, learner, deepseek_mode):
    cfg = _create(client, learner)
    _data(client.put(f"{API}/models/current", headers=learner, json={"modelId": cfg["id"]}))
    _data(client.delete(f"{API}/models/configs/{cfg['id']}", headers=learner))
    data = _data(client.get(f"{API}/models", headers=learner))
    assert data["current"] == settings.deepseek_model
    with SessionLocal() as db:
        assert db.get(UserModelChoice, "u_10001") is None


# ---------------------------------------------------------------------------- #
# 测试连通性
# ---------------------------------------------------------------------------- #
def test_probe_endpoint_with_form_values(client, learner, monkeypatch):
    seen: dict = {}

    def fake_probe(*, base_url, api_key, model_id, label=""):
        seen.update(base_url=base_url, api_key=api_key, model_id=model_id)
        return {"ok": True, "latencyMs": 5, "message": "连接成功（5ms）"}

    monkeypatch.setattr("app.api.v1.models.llm_userconf.probe", fake_probe)
    data = _data(
        client.post(
            f"{API}/models/configs/test",
            headers=learner,
            json={
                "baseUrl": "https://api-inference.modelscope.cn/v1",
                "modelId": "Qwen/Qwen3-32B",
                "apiKey": PLAIN_KEY,
            },
        )
    )
    assert data["ok"] is True
    assert seen["api_key"] == PLAIN_KEY
    # 缺参数 → 1001
    _data(
        client.post(f"{API}/models/configs/test", headers=learner, json={"modelId": "m"}),
        code=1001,
        status=400,
    )


def test_probe_endpoint_with_config_id_uses_stored_key(client, learner, monkeypatch):
    cfg = _create(client, learner)
    try:
        seen: dict = {}

        def fake_probe(*, base_url, api_key, model_id, label=""):
            seen.update(api_key=api_key, model_id=model_id)
            return {"ok": False, "latencyMs": 3, "message": "连接失败：401"}

        monkeypatch.setattr("app.api.v1.models.llm_userconf.probe", fake_probe)
        data = _data(
            client.post(
                f"{API}/models/configs/test", headers=learner, json={"configId": cfg["id"]}
            )
        )
        assert data["ok"] is False  # 测试动作本身 code 0，连通结果在 data.ok
        assert seen["api_key"] == PLAIN_KEY  # 用库中解密 key
    finally:
        _delete(client, learner, cfg["id"])


# ---------------------------------------------------------------------------- #
# key 日志/错误信息红线
# ---------------------------------------------------------------------------- #
def test_redact_and_log_mask():
    err = f"401 Unauthorized: bad key {PLAIN_KEY}"
    assert PLAIN_KEY not in crypto.redact(err, PLAIN_KEY)
    assert f"****{PLAIN_KEY[-4:]}" in crypto.redact(err, PLAIN_KEY)
    # 日志过滤器兜底：sk-/ms- 形态令牌被掩码
    masked = mask_pii(f"calling with sk-abcdef1234567890 and {PLAIN_KEY}")
    assert "sk-abcdef1234567890" not in masked
    assert PLAIN_KEY not in masked


def test_mock_mode_probe_offline(client, learner):
    """mock 基线下 probe 真实实现对不可达地址快速失败且信息脱敏（不依赖外网可达）。"""
    result = llm_userconf.probe(
        base_url="http://127.0.0.1:9",  # 不可达端口 → 连接错误
        api_key=PLAIN_KEY,
        model_id="test/model",
        label="离线探测",
    )
    assert result["ok"] is False
    assert PLAIN_KEY not in result["message"]
