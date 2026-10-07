"""文档学习后端主链路（接口文档第 20 章）。

覆盖 CC-document-learning 会话一验证清单：
1. 上传 PDF + md → 解析、分块入**该文档专属向量集合**（与内置库隔离）；
2. 基于文档生成讲义/图解/思维导图/练习题/闪卡 → 内容确实来自上传文档（含独有 token）；
   防幻觉（逐句接地）/ 内容安全生效；
3. 生成内容进「我的资源库」、带文档来源标识（source=document）；
4. 内置课程主线不受影响（内置资源库/检索回归）；mock 可跑（本套件 conftest 强制 mock）。
"""
from __future__ import annotations

import fitz  # pymupdf：仅测试内用于构造带已知文本的 PDF
import pytest
from fastapi.testclient import TestClient

from app.main import app

# 文档独有 token：内置课程语料里绝不会出现 → 用于证明「生成内容确实来自上传文档」
UNIQUE_MD = "泽塔向量收敛定理ZetaVec"
UNIQUE_PDF = "卡帕注意力机制KappaAttn"


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
        time.sleep(0.1)
    raise AssertionError("任务未在超时内完成")


def _make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    return doc.tobytes()


def _upload(client, headers, filename: str, raw: bytes) -> dict:
    res = client.post(
        "/api/v1/document/upload",
        headers=headers,
        files={"file": (filename, raw, "application/octet-stream")},
    )
    data = _data(res)
    task = _wait_task(client, headers, data["taskId"])
    assert task["status"] == "succeeded", task
    return data["document"]


@pytest.fixture()
def md_doc(client, learner) -> str:
    body = (
        f"# 泽塔向量收敛定理\n\n"
        f"{UNIQUE_MD} 指出：在 ZetaVec 空间中，权重按黄金比例衰减，从而保证收敛。\n\n"
        f"## 应用\n\n该定理用于稳定深层网络训练，是一种独有的收敛判据。\n"
    ).encode("utf-8")
    doc = _upload(client, learner, "泽塔讲义.md", body)
    assert doc["status"] in ("pending", "indexed")
    return doc["id"]


@pytest.fixture()
def pdf_doc(client, learner) -> str:
    text = (
        f"{UNIQUE_PDF} 是一种独有机制。卡帕注意力通过对查询向量施加 kappa 缩放，"
        f"强化关键 token 的权重分配，从而提升长序列建模能力。"
    )
    doc = _upload(client, learner, "卡帕注意力.pdf", _make_pdf(text))
    return doc["id"]


# ---- 1. 上传 + 解析 + 隔离入库 --------------------------------------------
def test_upload_parse_and_isolated_ingest(client, learner, md_doc, pdf_doc):
    """md + pdf 均成功解析、分块入专属集合（status=indexed, chunks>0）。"""
    docs = _data(client.get("/api/v1/document/list", headers=learner))
    ids = {d["id"]: d for d in docs["items"]}
    assert md_doc in ids and pdf_doc in ids
    for did in (md_doc, pdf_doc):
        detail = _data(client.get(f"/api/v1/document/{did}", headers=learner))
        assert detail["status"] == "indexed"
        assert detail["chunks"] >= 1
        assert detail["contentPreview"]


def test_collection_isolated_from_builtin_kb(client, learner, md_doc):
    """文档专属集合与内置 kb_chunks 物理隔离：文档独有 token 不进内置检索。"""
    from app.rag.vector_store import get_vector_store

    # 内置库全量语料里不应出现文档独有 token（不污染）
    builtin_corpus = " ".join(item["content"] for item in get_vector_store().get_all())
    assert UNIQUE_MD not in builtin_corpus


# ---- 2. 基于文档生成：内容来自文档 + 防幻觉/内容安全 -----------------------
def test_generate_lecture_from_document(client, learner, md_doc):
    """讲义内容取自文档（含独有 token）；sources 标注为文档来源；含逐句接地幻觉率。"""
    data = _data(
        client.post("/api/v1/document/generate/lecture", headers=learner,
                    json={"documentId": md_doc, "difficulty": "初级"})
    )
    assert data["docId"] == md_doc
    assert "ZetaVec" in data["markdown"] or "泽塔向量收敛定理" in data["markdown"]
    assert data["sources"] and all(s["type"] == "文档" for s in data["sources"])
    assert 0.0 <= data["hallucinationRate"] <= 1.0  # 防幻觉：逐句接地率


def test_generate_flashcards_from_document(client, learner, md_doc):
    """闪卡背面为文档原文要点（含独有 token）。"""
    data = _data(
        client.post("/api/v1/document/generate/flashcards", headers=learner,
                    json={"documentId": md_doc, "count": 5})
    )
    assert data["cards"]
    joined = " ".join(c["front"] + c["back"] for c in data["cards"])
    assert "ZetaVec" in joined or "泽塔" in joined


def test_generate_quiz_from_document(client, learner, md_doc):
    """练习题取自文档（含独有 token），且答案自洽（audit_practice 通过）。"""
    from app.core.llm import audit_practice

    data = _data(
        client.post("/api/v1/document/generate/quiz", headers=learner,
                    json={"documentId": md_doc, "count": 3})
    )
    assert data["questions"]
    assert all(not audit_practice(q) for q in data["questions"])  # 防错题：答案自洽
    joined = " ".join(q["question_text"] for q in data["questions"])
    assert "ZetaVec" in joined or "泽塔" in joined


def test_generate_diagram_and_mindmap_and_video(client, learner, md_doc, pdf_doc):
    """图解/思维导图/视频均产出契约结构；思维导图含文档标题。"""
    diagram = _data(
        client.post("/api/v1/document/generate/diagram", headers=learner,
                    json={"documentId": md_doc})
    )
    assert diagram["mermaid"].strip()

    mindmap = _data(
        client.post("/api/v1/document/generate/mindmap", headers=learner,
                    json={"documentId": md_doc})
    )
    assert mindmap["markdown"].startswith("#")

    video = _data(
        client.post("/api/v1/document/generate/video", headers=learner,
                    json={"documentId": pdf_doc, "difficulty": "初级"})
    )
    assert video["videoUrl"] is None
    assert video["scenes"] and video["durationInFrames"] > 0


# ---- 3. 进资源库 + 文档来源标识 -------------------------------------------
def test_document_generation_enters_resource_library(client, learner, md_doc):
    """文档学习产物进「我的资源库」，带 source=document + docId/docTitle。"""
    client.post("/api/v1/document/generate/lecture", headers=learner,
                json={"documentId": md_doc, "difficulty": "初级"})
    client.post("/api/v1/document/generate/flashcards", headers=learner,
                json={"documentId": md_doc, "count": 3})

    data = _data(client.get("/api/v1/resource/history", headers=learner,
                            params={"pageSize": 100, "source": "document"}))
    doc_rows = [i for i in data["items"] if i["docId"] == md_doc]
    assert doc_rows, "文档产物应进入资源库"
    assert all(i["source"] == "document" for i in doc_rows)
    kinds = {i["kind"] for i in doc_rows}
    assert {"lecture", "flashcard"} <= kinds
    assert all(i["docTitle"] for i in doc_rows)


# ---- 4. 内置课程主线回归（不受影响） --------------------------------------
def test_builtin_course_not_affected(client, learner):
    """内置讲义/图解仍正常；内置资源库 source=builtin，与文档链路互不干扰。"""
    lec = _data(client.post("/api/v1/resource/lecture", headers=learner,
                            json={"kpId": "nn", "difficulty": "初级"}))
    assert lec["kpId"] == "nn" and lec["markdown"]
    client.get("/api/v1/resource/diagram/nn", headers=learner)

    builtin = _data(client.get("/api/v1/resource/history", headers=learner,
                               params={"pageSize": 100, "source": "builtin"}))
    nn_rows = [i for i in builtin["items"] if i["kpId"] == "nn"]
    assert nn_rows
    assert all(i["source"] == "builtin" for i in nn_rows)
    # 内置行携带知识点名（回归：不被文档链路改动破坏）
    assert any(i["kpName"] == "神经网络基础" for i in nn_rows)


# ---- 6. 多文档统一生成（合并检索范围，来源体现来自多篇） -------------------
def test_multi_doc_unified_generation(client, learner, md_doc, pdf_doc):
    """documentIds 传两篇 → 统一生成，来源标注同时含两篇文档标题（内容确来自 2 个文档）。"""
    data = _data(
        client.post(
            "/api/v1/document/generate/lecture",
            headers=learner,
            json={"documentId": md_doc, "documentIds": [md_doc, pdf_doc], "difficulty": "初级"},
        )
    )
    assert data["docId"] == md_doc  # 主文档
    assert set(data["docIds"]) == {md_doc, pdf_doc}
    titles = " ".join(s["title"] for s in data["sources"])
    # 来源同时出现两篇文档（证明合并检索范围，而非只用其一）
    assert "泽塔" in titles and "卡帕" in titles
    assert all(s["type"] == "文档" for s in data["sources"])


def test_multi_doc_mindmap_lists_both(client, learner, md_doc, pdf_doc):
    """多篇思维导图以各文档标题为二级节点（体现来自多篇）。"""
    data = _data(
        client.post(
            "/api/v1/document/generate/mindmap",
            headers=learner,
            json={"documentId": md_doc, "documentIds": [md_doc, pdf_doc]},
        )
    )
    assert "泽塔" in data["markdown"] and "卡帕" in data["markdown"]


# ---- 7. 和文档对话（严格基于文档 + 溯源 + 流式） ---------------------------
def test_doc_chat_json_grounded_with_sources(client, learner, md_doc):
    """非流式问答：答案取自文档（含独有 token）、带溯源 sources、行内 [n] 标注。"""
    data = _data(
        client.post(
            "/api/v1/document/chat",
            headers=learner,
            json={"documentId": md_doc, "message": "这篇文档的核心定理是什么？"},
        )
    )
    assert data["sessionId"]
    assert data["reply"].strip()
    assert "ZetaVec" in data["reply"] or "泽塔" in data["reply"]  # 严格基于文档
    assert "[1]" in data["reply"]  # 行内溯源标注
    assert data["sources"] and all(s["type"] == "文档" for s in data["sources"])


def test_doc_chat_multi_doc_json(client, learner, md_doc, pdf_doc):
    """多篇统一问答：溯源覆盖两篇文档。"""
    data = _data(
        client.post(
            "/api/v1/document/chat",
            headers=learner,
            json={
                "documentId": md_doc,
                "documentIds": [md_doc, pdf_doc],
                "message": "这两篇资料分别讲了什么？",
            },
        )
    )
    titles = " ".join(s["title"] for s in data["sources"])
    assert "泽塔" in titles and "卡帕" in titles


def test_doc_chat_sse_stream(client, learner, md_doc):
    """SSE 流式问答：逐 delta 下发 + event: done 携带 sources。"""
    res = client.post(
        "/api/v1/document/chat",
        headers={**learner, "Accept": "text/event-stream"},
        json={"documentId": md_doc, "message": "核心定理是什么？"},
    )
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")
    body = res.text
    assert '"delta"' in body  # 逐字增量
    assert "event: done" in body  # 正常收尾
    assert '"sources"' in body  # done 事件携带溯源


def test_doc_chat_ownership(client, learner, md_doc):
    """他人不可就本人文档发起问答 → 1004。"""
    admin = _login(client, "admin", "admin123")
    res = client.post(
        "/api/v1/document/chat",
        headers=admin,
        json={"documentId": md_doc, "message": "讲了什么？"},
    )
    assert res.json()["code"] == 1004


# ---- 5. 归属与鉴权 --------------------------------------------------------
def test_document_ownership_and_auth(client, learner, md_doc):
    """他人不可访问/生成；未登录 → 1002；不存在文档 → 1004。"""
    admin = _login(client, "admin", "admin123")
    # admin 访问 learner 的文档 → 1004（归属隔离）
    client.get(f"/api/v1/document/{md_doc}", headers=admin).json()["code"] == 1004
    res = client.post("/api/v1/document/generate/lecture", headers=admin,
                      json={"documentId": md_doc, "difficulty": "初级"})
    assert res.json()["code"] == 1004
    # 未登录
    assert client.get("/api/v1/document/list").json()["code"] == 1002
    # 不存在
    assert client.get("/api/v1/document/udoc_nope", headers=learner).json()["code"] == 1004
