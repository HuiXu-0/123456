"""我的资源库·生成历史（接口文档第 19 章）。

覆盖：
- 生成讲义/视频/图解 → 各自接口旁路埋点 → GET /resource/history 列出当前用户资产；
- POST /resource/history/log 为 mindmap/code 补埋点，且 upsert 幂等（重复不新增行）；
- 过滤：kind / kpId 精确过滤；
- 用户隔离：A 用户看不到 B 用户的记录；
- 非白名单 kind 静默忽略（不入库、不报错）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    res = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture()
def learner_headers(client) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


def _history(client, headers, **params) -> dict:
    res = client.get("/api/v1/resource/history", headers=headers, params=params)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0
    return body["data"]


def test_generate_then_history_lists_user_records(client, learner_headers):
    """生成多类内容后，资源库按用户列出（含 kpName/kind/difficulty/createdAt），倒序分页。"""
    client.post("/api/v1/resource/lecture", headers=learner_headers, json={"kpId": "nn", "difficulty": "初级"})
    client.post("/api/v1/resource/video", headers=learner_headers, json={"kpId": "nn", "difficulty": "初级"})
    client.get("/api/v1/resource/diagram/nn", headers=learner_headers)

    data = _history(client, learner_headers, pageSize=50)
    kinds = {(i["kpId"], i["kind"]) for i in data["items"]}
    assert ("nn", "lecture") in kinds
    assert ("nn", "video") in kinds
    assert ("nn", "diagram") in kinds
    # 契约字段齐全 + kpName 回填
    one = next(i for i in data["items"] if i["kind"] == "lecture" and i["kpId"] == "nn")
    for key in ("id", "kpId", "kpName", "kind", "difficulty", "title", "resourceRef", "createdAt"):
        assert key in one
    assert one["kpName"] == "神经网络基础"
    assert one["difficulty"] == "初级"


def test_log_endpoint_covers_mindmap_and_code_and_is_idempotent(client, learner_headers):
    """POST /resource/history/log 补埋 mindmap/code；重复调用 upsert 幂等（不新增行）。"""
    for _ in range(3):
        res = client.post(
            "/api/v1/resource/history/log",
            headers=learner_headers,
            json={"kpId": "cnn", "kind": "code", "difficulty": ""},
        )
        assert res.status_code == 200 and res.json()["code"] == 0
    client.post(
        "/api/v1/resource/history/log",
        headers=learner_headers,
        json={"kpId": "cnn", "kind": "mindmap", "difficulty": ""},
    )
    data = _history(client, learner_headers, pageSize=100, kpId="cnn")
    code_rows = [i for i in data["items"] if i["kind"] == "code" and i["kpId"] == "cnn"]
    assert len(code_rows) == 1  # upsert：3 次调用仍只一行
    assert any(i["kind"] == "mindmap" for i in data["items"])


def test_kind_filter(client, learner_headers):
    """kind 过滤只回该形态。"""
    client.post("/api/v1/resource/lecture", headers=learner_headers, json={"kpId": "dl", "difficulty": "初级"})
    client.get("/api/v1/resource/diagram/dl", headers=learner_headers)
    data = _history(client, learner_headers, pageSize=100, kind="diagram")
    assert data["items"], "应至少有一条 diagram 记录"
    assert all(i["kind"] == "diagram" for i in data["items"])


def test_time_filter(client, learner_headers):
    """时间过滤：未来起点 → 空；很早起点 → 含记录。covers 带时区入参与 naive-UTC 列的可比。"""
    client.post("/api/v1/resource/lecture", headers=learner_headers, json={"kpId": "ml", "difficulty": "入门"})
    future = _history(client, learner_headers, pageSize=100, startTime="2099-01-01T00:00:00Z")
    assert future["total"] == 0
    past = _history(client, learner_headers, pageSize=100, startTime="2000-01-01")
    assert past["total"] > 0


def test_unknown_kind_is_ignored(client, learner_headers):
    """非白名单 kind 静默忽略：接口 200，但不产生记录。"""
    res = client.post(
        "/api/v1/resource/history/log",
        headers=learner_headers,
        json={"kpId": "nn", "kind": "banana", "difficulty": ""},
    )
    assert res.status_code == 200 and res.json()["code"] == 0
    data = _history(client, learner_headers, pageSize=100, kpId="nn")
    assert all(i["kind"] != "banana" for i in data["items"])


def test_user_isolation(client):
    """A 用户的记录不出现在 B（admin）用户的资源库里。"""
    learner = _login(client, "learner_001", "123456")
    admin = _login(client, "admin", "admin123")
    client.post("/api/v1/resource/history/log", headers=learner, json={"kpId": "transformer", "kind": "code"})
    admin_data = _history(client, admin, pageSize=100, kpId="transformer")
    assert all(not (i["kpId"] == "transformer" and i["kind"] == "code") for i in admin_data["items"])


def test_history_requires_auth(client):
    """未登录 → 1002。"""
    res = client.get("/api/v1/resource/history")
    assert res.json()["code"] == 1002
