"""B4-b 契约测试：14.5 Prompt 模板（GET/PUT 热更新）+ 14.6 系统指标看板。

验收口径（接口文档 14.5/14.6 + CLAUDE.md 信封约定）：
- GET /admin/prompts/{agentId}：返回 PromptTemplate 全量字段；agentId 固定 3 项；
- PUT：version 自增、updatedAt 刷新、hotReloaded=true，重新 GET 内容生效；
- 缺失必需占位符 → 1001/400；未知 agentId → 1004/404；
- GET /admin/metrics：三比率 + 三计数 + updatedAt，结构定死（B8 接真实计算）；
- learner 访问三接口均 → 1003/403。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

AGENT_IDS = ["diagnosis", "generation", "critic"]


@pytest.fixture(scope="module")
def client():
    # with 触发 lifespan：建表 + 幂等种子
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    res = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    token = res.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin_headers(client: TestClient) -> dict[str, str]:
    return _login(client, "admin", "admin123")


@pytest.fixture(scope="module")
def learner_headers(client: TestClient) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


# ---- 鉴权：learner 访问三接口均 1003 ---------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/admin/prompts/generation"),
        ("PUT", "/api/v1/admin/prompts/generation"),
        ("GET", "/api/v1/admin/metrics"),
    ],
)
def test_learner_forbidden(client, learner_headers, method, path):
    kwargs = {"headers": learner_headers}
    if method == "PUT":
        kwargs["json"] = {"template": "x"}
    res = client.request(method, path, **kwargs)
    assert res.status_code == 403
    assert res.json()["code"] == 1003


# ---- 14.5 GET ---------------------------------------------------------------

@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_get_prompt_template_shape(client, admin_headers, agent_id):
    res = client.get(f"/api/v1/admin/prompts/{agent_id}", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["agentId"] == agent_id
    assert isinstance(data["name"], str) and data["name"]
    assert isinstance(data["template"], str) and data["template"]
    assert isinstance(data["variables"], list) and data["variables"]
    assert isinstance(data["version"], int) and data["version"] >= 1
    assert data["updatedAt"]
    # 模板中包含全部声明的占位符
    for var in data["variables"]:
        assert f"{{{var}}}" in data["template"]


def test_get_prompt_unknown_agent_404(client, admin_headers):
    res = client.get("/api/v1/admin/prompts/nope", headers=admin_headers)
    assert res.status_code == 404
    assert res.json()["code"] == 1004


# ---- 14.5 PUT 热更新 ---------------------------------------------------------

def test_put_prompt_version_increment_and_effective(client, admin_headers):
    before = client.get(
        "/api/v1/admin/prompts/generation", headers=admin_headers
    ).json()["data"]
    # 新模板需保留全部必需占位符（B8 起 generation 含 {description} 核心概念清单）
    placeholders = "\n".join(f"{{{var}}}" for var in before["variables"])
    new_template = f"你是领域知识讲义生成专家（契约测试改写）。\n{placeholders}"
    res = client.put(
        "/api/v1/admin/prompts/generation",
        headers=admin_headers,
        json={"template": new_template},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["agentId"] == "generation"
    assert data["version"] == before["version"] + 1
    assert data["hotReloaded"] is True
    assert data["updatedAt"] and data["updatedAt"] >= before["updatedAt"]

    # 重新 GET：内容生效、版本一致（热更新供 B5 消费的同一读取路径）
    after = client.get(
        "/api/v1/admin/prompts/generation", headers=admin_headers
    ).json()["data"]
    assert after["template"] == new_template
    assert after["version"] == before["version"] + 1
    assert after["variables"] == before["variables"]

    # 复原原模板（不在 dev 库残留测试改写内容）
    restore = client.put(
        "/api/v1/admin/prompts/generation",
        headers=admin_headers,
        json={"template": before["template"]},
    )
    assert restore.status_code == 200, restore.text


def test_put_prompt_missing_placeholder_400(client, admin_headers):
    before = client.get(
        "/api/v1/admin/prompts/generation", headers=admin_headers
    ).json()["data"]
    res = client.put(
        "/api/v1/admin/prompts/generation",
        headers=admin_headers,
        json={"template": "缺占位符的模板，只有 {kpName}"},
    )
    assert res.status_code == 400
    assert res.json()["code"] == 1001
    # 版本不变、内容不变
    after = client.get(
        "/api/v1/admin/prompts/generation", headers=admin_headers
    ).json()["data"]
    assert after["version"] == before["version"]
    assert after["template"] == before["template"]


def test_put_prompt_unknown_agent_404(client, admin_headers):
    res = client.put(
        "/api/v1/admin/prompts/nope",
        headers=admin_headers,
        json={"template": "x"},
    )
    assert res.status_code == 404
    assert res.json()["code"] == 1004


# ---- 14.6 指标看板 -----------------------------------------------------------

def test_get_metrics_contract_shape(client, admin_headers):
    res = client.get("/api/v1/admin/metrics", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0
    data = body["data"]
    for rate_key in ("hallucinationRate", "adaptationRate", "coverageRate"):
        assert isinstance(data[rate_key], float)
        assert 0.0 <= data[rate_key] <= 1.0
    for count_key in ("kbDocuments", "kbChunks", "generatedResources"):
        assert isinstance(data[count_key], int)
        assert data[count_key] >= 0
    assert data["updatedAt"]
