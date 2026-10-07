"""智能辅导·按需资源生成 契约测试（接口文档 8.8，C-fix 批3-bonus）。

- POST /resource/tutor/suggest：识别问题点 + 资源生成清单（type 白名单、题点因问而异）。
- POST /resource/tutor/generate：勾选类型 → 复用既有能力真实生成对应资源（可查看）。
- 需登录；知识点不存在 → 1004；mock 双模式可跑。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

REMEDIAL_TYPES = {"diagram", "example", "video", "lecture"}
SUGGEST_KEYS = {"kpId", "kpName", "problemPoint", "suggestions", "generationMeta"}
SUGGESTION_KEYS = {"id", "type", "title", "expect"}


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


def test_suggest_requires_login(client):
    assert client.post("/api/v1/resource/tutor/suggest", json={"kpId": "nn", "question": "x"}).status_code == 401


def test_suggest_unknown_kp(client, headers):
    res = client.post("/api/v1/resource/tutor/suggest", headers=headers, json={"kpId": "zzz", "question": "x"})
    assert res.status_code == 404
    assert res.json()["code"] == 1004


def test_suggest_identifies_problem_and_lists_resources(client, headers):
    data = _data(client.post(
        "/api/v1/resource/tutor/suggest",
        headers=headers,
        json={"kpId": "nn", "question": "激活函数到底有什么用，我没懂"},
    ))
    assert set(data) == SUGGEST_KEYS
    assert isinstance(data["problemPoint"], str) and data["problemPoint"]
    assert isinstance(data["suggestions"], list) and len(data["suggestions"]) >= 2
    types_seen = set()
    for s in data["suggestions"]:
        assert set(s) == SUGGESTION_KEYS
        assert s["type"] in REMEDIAL_TYPES
        assert s["title"] and s["expect"]
        types_seen.add(s["type"])
    assert len(types_seen) == len(data["suggestions"])  # type 去重


def test_generate_selected_resources(client, headers):
    """勾选 图解 + 例题 + 补充讲义 → 复用既有能力真实生成对应资源（可查看）。"""
    data = _data(client.post(
        "/api/v1/resource/tutor/generate",
        headers=headers,
        json={"kpId": "nn", "problemPoint": "激活函数与非线性", "types": ["diagram", "example", "lecture"]},
    ))
    assert data["kpId"] == "nn" and data["problemPoint"] == "激活函数与非线性"
    by_type = {r["type"]: r for r in data["results"]}
    assert set(by_type) == {"diagram", "example", "lecture"}
    # 图解复用 8.5 → mermaid 流程图
    assert by_type["diagram"]["mermaid"].strip().lower().startswith(("flowchart", "graph"))
    # 例题：题干 + 解析
    assert by_type["example"]["statement"] and by_type["example"]["solution"]
    # 补充讲义片段：markdown
    assert by_type["lecture"]["markdown"].strip()


def test_generate_filters_unknown_types_and_dedups(client, headers):
    data = _data(client.post(
        "/api/v1/resource/tutor/generate",
        headers=headers,
        json={"kpId": "nn", "types": ["example", "bogus", "example"]},
    ))
    # 未知类型剔除、去重 → 仅 1 项 example；problemPoint 缺省回落知识点核心概念
    assert [r["type"] for r in data["results"]] == ["example"]
    assert data["problemPoint"]


def test_generate_empty_selection(client, headers):
    data = _data(client.post(
        "/api/v1/resource/tutor/generate",
        headers=headers,
        json={"kpId": "nn", "types": []},
    ))
    assert data["results"] == []
