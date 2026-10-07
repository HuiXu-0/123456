"""契约测试：修改密码（接口文档 3.4 / 16.5）。

验收口径：
- 已登录用户旧密码正确 → 改成功（code 0），用新密码登录通过；
- 旧密码错误 → code 1001 / HTTP 400，message="旧密码错误"，密码不变（旧/新密码均不能登录原账号侧验证）；
- 新密码过弱（<6）→ code 1001 / HTTP 400，message="密码长度至少为 6 位"；
- 未携带 token → code 1002 / HTTP 401（复用 get_current_user 鉴权）；
- 回归：既有 /auth/login 行为逐字未变（旧密码改后失效、新密码生效）。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, password: str = "123456") -> tuple[str, str]:
    """注册全新学习者，返回 (username, token)。"""
    username = f"cpw_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    return username, res.json()["data"]["token"]


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


def test_change_password_success_then_login_with_new(client: TestClient):
    username, token = _register(client, "oldpass1")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post(
        "/api/v1/auth/change-password",
        json={"oldPassword": "oldpass1", "newPassword": "newpass2"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0
    assert body["data"]["userId"]
    assert body["data"]["changed"] is True

    # 新密码可登录
    ok = _login(client, username, "newpass2")
    assert ok.status_code == 200, ok.text
    assert ok.json()["code"] == 0

    # 旧密码已失效（回归 /auth/login：错误口令仍 1002/401）
    bad = _login(client, username, "oldpass1")
    assert bad.status_code == 401
    assert bad.json()["code"] == 1002


def test_change_password_wrong_old_password(client: TestClient):
    username, token = _register(client, "oldpass1")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post(
        "/api/v1/auth/change-password",
        json={"oldPassword": "WRONG", "newPassword": "newpass2"},
        headers=headers,
    )
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == 1001
    assert body["message"] == "旧密码错误"

    # 密码未被改动：原密码仍可登录，新密码不可
    assert _login(client, username, "oldpass1").status_code == 200
    assert _login(client, username, "newpass2").status_code == 401


def test_change_password_weak_new_password(client: TestClient):
    _username, token = _register(client, "oldpass1")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post(
        "/api/v1/auth/change-password",
        json={"oldPassword": "oldpass1", "newPassword": "123"},
        headers=headers,
    )
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == 1001
    assert body["message"] == "密码长度至少为 6 位"


def test_change_password_requires_auth(client: TestClient):
    res = client.post(
        "/api/v1/auth/change-password",
        json={"oldPassword": "oldpass1", "newPassword": "newpass2"},
    )
    assert res.status_code == 401
    assert res.json()["code"] == 1002
