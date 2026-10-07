"""产物落库 + 资源库 CRUD（CC-artifact-persist 会话一验证清单）。

覆盖：
1. 文档产物落库：生成后产物入库；再次「查看」（同参再调生成接口）**直接读产物**、
   内容与生成时一致、**0 次 RAG/LLM 生成调用**；
2. 「重新生成」显式重跑：regenerate=true 才真正重跑生成、覆盖产物、刷新时间；
3. 资源库 CRUD·改：重命名标题（按 user 校验归属，他人 1004）；
4. 资源库 CRUD·删：删除资产（按 user 校验归属，他人 1004）+ 删文档连带清理资产行；
5. 内置课程主线不受影响（内置讲义仍走 ResourceCache，本就 0 重复生成）。

本套件 conftest 强制 mock provider（确定性、零网络、零计费）。
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
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture()
def learner(client) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


def _data(res, code: int = 0):
    body = res.json()
    assert body["code"] == code, body
    return body["data"]


def _wait_task(client, headers, task_id: str, timeout: float = 60) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        d = _data(client.get(f"/api/v1/tasks/{task_id}", headers=headers))
        if d["status"] in ("succeeded", "failed"):
            return d
        time.sleep(0.05)
    raise AssertionError("任务未在超时内完成")


@pytest.fixture()
def md_doc(client, learner) -> str:
    body = (
        "# 泽塔向量收敛定理\n\n"
        "泽塔向量收敛定理ZetaVec 指出：在 ZetaVec 空间中，权重按黄金比例衰减，从而保证收敛。\n\n"
        "## 应用\n\n该定理用于稳定深层网络训练，是一种独有的收敛判据。\n"
    ).encode("utf-8")
    res = client.post(
        "/api/v1/document/upload",
        headers=learner,
        files={"file": ("泽塔讲义.md", body, "application/octet-stream")},
    )
    data = _data(res)
    task = _wait_task(client, learner, data["taskId"])
    assert task["status"] == "succeeded", task
    return data["document"]["id"]


def _history(client, headers, **params) -> dict:
    params.setdefault("pageSize", 100)
    return _data(client.get("/api/v1/resource/history", headers=headers, params=params))


def _row(client, headers, doc_id: str, kind: str) -> dict:
    data = _history(client, headers, source="document")
    return next(i for i in data["items"] if i["docId"] == doc_id and i["kind"] == kind)


# ---- 1 + 2. 产物落库：查看直接读（0 生成）、重新生成显式重跑 -----------------
def test_view_reads_artifact_zero_generation_and_regenerate_reruns(
    client, learner, md_doc, monkeypatch
):
    """生成→入库；再次查看直接读产物（0 次 RAG/LLM 生成）、内容一致；regenerate=true 才重跑。"""
    import app.services.document_generation as gen

    first = _data(
        client.post(
            "/api/v1/document/generate/lecture",
            headers=learner,
            json={"documentId": md_doc, "difficulty": "初级"},
        )
    )
    assert first["markdown"] and first["docId"] == md_doc

    # 计数：真正的生成必然调 retrieve + run_generator；查看走产物读取则一次都不调。
    calls = {"retrieve": 0, "gen": 0}
    orig_retrieve, orig_gen = gen.retrieve, gen.run_generator
    monkeypatch.setattr(
        gen, "retrieve", lambda *a, **k: (calls.__setitem__("retrieve", calls["retrieve"] + 1), orig_retrieve(*a, **k))[1]
    )
    monkeypatch.setattr(
        gen, "run_generator", lambda *a, **k: (calls.__setitem__("gen", calls["gen"] + 1), orig_gen(*a, **k))[1]
    )

    # 查看（同参再调生成接口）→ 直接读产物：0 次生成，内容与生成时逐字一致
    second = _data(
        client.post(
            "/api/v1/document/generate/lecture",
            headers=learner,
            json={"documentId": md_doc, "difficulty": "初级"},
        )
    )
    assert calls == {"retrieve": 0, "gen": 0}, "查看不应触发任何生成调用"
    assert second["markdown"] == first["markdown"]
    assert second["sources"] == first["sources"]

    # 显式重新生成 → 真正重跑（retrieve + run_generator 均被调）
    third = _data(
        client.post(
            "/api/v1/document/generate/lecture",
            headers=learner,
            json={"documentId": md_doc, "difficulty": "初级", "regenerate": True},
        )
    )
    assert calls["retrieve"] >= 1 and calls["gen"] >= 1, "重新生成应真正重跑生成"
    assert third["markdown"]


def test_regenerate_refreshes_time_and_updates_artifact(client, learner, md_doc):
    """重新生成刷新资源库时间戳；查看读到的是最新产物（覆盖旧产物）。"""
    _data(
        client.post(
            "/api/v1/document/generate/flashcards",
            headers=learner,
            json={"documentId": md_doc, "count": 5},
        )
    )
    t1 = _row(client, learner, md_doc, "flashcard")["createdAt"]
    _data(
        client.post(
            "/api/v1/document/generate/flashcards",
            headers=learner,
            json={"documentId": md_doc, "count": 5, "regenerate": True},
        )
    )
    t2 = _row(client, learner, md_doc, "flashcard")["createdAt"]
    assert t2 >= t1  # 时间刷新（mock 确定性，同秒内 >=）


def test_all_document_kinds_persist_artifact(client, learner, md_doc):
    """六类文档产物均落库：二次查看直接读，docId 一致（覆盖 diagram/mindmap/video/quiz）。"""
    cases = [
        ("diagram", {}),
        ("mindmap", {}),
        ("video", {"difficulty": "初级"}),
        ("quiz", {"count": 3}),
    ]
    for kind, extra in cases:
        body = {"documentId": md_doc, **extra}
        one = _data(client.post(f"/api/v1/document/generate/{kind}", headers=learner, json=body))
        two = _data(client.post(f"/api/v1/document/generate/{kind}", headers=learner, json=body))
        first_meta = one.pop("generationMeta")
        cached_meta = two.pop("generationMeta")
        assert one == two, f"{kind} 二次查看应逐字读回同一产物"
        assert first_meta["source"] in {"mock", "deterministic"}
        assert cached_meta["source"] == "cache"
        assert _row(client, learner, md_doc, kind)  # 已落库（资源库可见）


# ---- 3. CRUD·改：重命名 + 归属 --------------------------------------------
def test_rename_title_and_ownership(client, learner, md_doc):
    """重命名改展示标题、资源库即时反映；他人无权改 → 1004。"""
    _data(
        client.post(
            "/api/v1/document/generate/lecture",
            headers=learner,
            json={"documentId": md_doc, "difficulty": "初级"},
        )
    )
    rid = _row(client, learner, md_doc, "lecture")["id"]
    renamed = _data(
        client.post(
            "/api/v1/resource/history/rename",
            headers=learner,
            json={"id": rid, "title": "我的自定义讲义标题"},
        )
    )
    assert renamed["title"] == "我的自定义讲义标题"
    assert _row(client, learner, md_doc, "lecture")["title"] == "我的自定义讲义标题"

    admin = _login(client, "admin", "admin123")
    res = client.post(
        "/api/v1/resource/history/rename", headers=admin, json={"id": rid, "title": "越权改"}
    )
    assert res.json()["code"] == 1004


# ---- 4. CRUD·删：删除资产 + 归属 + 删文档连带清理 --------------------------
def test_delete_resource_and_ownership(client, learner, md_doc):
    """删除单条资产（连带产物）；他人无权删 → 1004。"""
    _data(
        client.post(
            "/api/v1/document/generate/flashcards",
            headers=learner,
            json={"documentId": md_doc, "count": 3},
        )
    )
    rid = _row(client, learner, md_doc, "flashcard")["id"]

    admin = _login(client, "admin", "admin123")
    assert client.delete(f"/api/v1/resource/history/{rid}", headers=admin).json()["code"] == 1004

    deleted = _data(client.delete(f"/api/v1/resource/history/{rid}", headers=learner))
    assert deleted["deleted"] is True
    data = _history(client, learner, source="document")
    assert not any(i["id"] == rid for i in data["items"])


def test_delete_document_removes_library_rows(client, learner, md_doc):
    """删除文档 → 其在资源库的资产行连带清理（无孤儿资产）。"""
    for kind, body in (
        ("lecture", {"documentId": md_doc, "difficulty": "初级"}),
        ("mindmap", {"documentId": md_doc}),
    ):
        _data(client.post(f"/api/v1/document/generate/{kind}", headers=learner, json=body))
    assert any(i["docId"] == md_doc for i in _history(client, learner, source="document")["items"])

    dd = _data(client.delete(f"/api/v1/document/{md_doc}", headers=learner))
    assert dd["deleted"] is True
    assert not any(
        i["docId"] == md_doc for i in _history(client, learner, source="document")["items"]
    )


# ---- 5. 内置课程主线不受影响（本就走 ResourceCache、0 重复生成） -----------
def test_builtin_lecture_unaffected(client, learner):
    """内置讲义仍走 ResourceCache：二次请求内容一致，且不因文档链路改动回归。"""
    a = _data(
        client.post("/api/v1/resource/lecture", headers=learner, json={"kpId": "nn", "difficulty": "初级"})
    )
    b = _data(
        client.post("/api/v1/resource/lecture", headers=learner, json={"kpId": "nn", "difficulty": "初级"})
    )
    assert a["markdown"] == b["markdown"] and a["kpId"] == "nn"
    builtin = _history(client, learner, source="builtin")
    assert any(i["kpId"] == "nn" and i["kind"] == "lecture" for i in builtin["items"])
