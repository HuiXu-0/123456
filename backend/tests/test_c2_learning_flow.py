"""C2 契约测试：学习流程模块（费曼 + 康奈尔，接口文档第 18 章）。

验收口径（接口文档 18.1-18.6）：
- 18.1 cornell-cues：返回真实线索问题（5-8 条、type 枚举）+ noteOutline + summaryHint
  + sources（复用 8.2）；难度/知识点非法 → 1001/1004；mock/真实双模式结构一致。
- 18.2 feynman：JSON 点评 + gaps（review[] 复用 6.3 指向真实资源端点）+ score/
  followups/complete；Accept: text/event-stream → SSE（delta + event:gaps + event:done）；
  会话内存 TTL；mock/真实双模式。
- 18.3 steps：6 步过程标记读写，透传 7.1 mastery；非法步骤 → 1001。
- 18.4/18.5 notes：康奈尔三栏读写、整体覆盖。
- 18.6 掌握度对接：阶段测试（9.1）通过 → Mastery 真实 passed，steps.mastery 透传。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core import llm as llm_mod
from app.core import llm_deepseek
from app.core.config import settings
from app.main import app
from app.services import learning_flow as learning_service

CORNELL_KEYS = {"kpId", "difficulty", "cues", "noteOutline", "summaryHint", "sources", "generationMeta"}
FEYNMAN_JSON_KEYS = {"sessionId", "feedback", "score", "gaps", "followups", "complete", "generationMeta"}
STEP_KEYS = {"video", "lecture", "diagram", "note", "feynman", "practice"}
STEPS_RESP_KEYS = {"kpId", "steps", "mastery", "completedCount", "total", "updatedAt"}
NOTE_KEYS = {"kpId", "cueNotes", "mainNotes", "summary", "updatedAt"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    res = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture(scope="module")
def learner_headers(client) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


@pytest.fixture(scope="module")
def fresh_headers(client) -> dict[str, str]:
    """注册一个独立学习者，隔离 steps/notes/mastery 状态（不受其它用例累积影响）。"""
    res = client.post(
        "/api/v1/auth/register",
        json={"username": "c2_learner", "password": "123456"},
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture(autouse=True)
def _no_stream_delay():
    """SSE 测试零字间延迟。"""
    delay = settings.tutor_stream_delay_ms
    settings.tutor_stream_delay_ms = 0
    yield
    settings.tutor_stream_delay_ms = delay


def _parse_sse(text: str) -> tuple[list[dict], dict[str, dict]]:
    """解析 SSE 文本 → (默认 delta 列表, {事件名: data})。"""
    deltas: list[dict] = []
    named: dict[str, dict] = {}
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = None
        data_line = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data_line = line[len("data: "):]
        assert data_line is not None, f"SSE 块缺 data 行：{block!r}"
        payload = json.loads(data_line)
        (deltas.append(payload) if event is None else named.__setitem__(event, payload))
    return deltas, named


# ---- 18.1 康奈尔线索生成 ------------------------------------------------------

def test_cornell_cues_returns_real_questions(client, learner_headers):
    res = client.post(
        "/api/v1/learning/cornell-cues",
        headers=learner_headers,
        json={"kpId": "nn", "difficulty": "初级"},
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert set(data.keys()) == CORNELL_KEYS
    assert data["kpId"] == "nn" and data["difficulty"] == "初级"
    cues = data["cues"]
    assert 5 <= len(cues) <= 8  # 契约 5-8 条
    for c in cues:
        assert set(c.keys()) == {"id", "type", "text"}
        assert c["type"] in ("question", "keyword")
        assert c["text"].strip()
    # 真实线索问题（非占位）：含至少一条针对 nn 的问题
    assert any(c["type"] == "question" for c in cues)
    assert any("激活函数" in c["text"] for c in cues)
    # noteOutline 预填骨架
    for n in data["noteOutline"]:
        assert set(n.keys()) == {"id", "cueId", "heading", "points"}
        assert n["heading"] and isinstance(n["points"], list)
    assert data["summaryHint"]
    # sources 复用 8.2（title/type/confidence）
    assert data["sources"] and all(
        {"title", "type", "confidence"} <= set(s.keys()) for s in data["sources"]
    )


def test_cornell_cues_default_difficulty_and_per_topic(client, learner_headers):
    # 难度缺省「初级」
    res = client.post(
        "/api/v1/learning/cornell-cues", headers=learner_headers, json={"kpId": "transformer"}
    )
    data = res.json()["data"]
    assert data["difficulty"] == "初级"
    # 线索随知识点变化：transformer 不等于 nn
    nn = client.post(
        "/api/v1/learning/cornell-cues", headers=learner_headers, json={"kpId": "nn"}
    ).json()["data"]
    assert [c["text"] for c in data["cues"]] != [c["text"] for c in nn["cues"]]


def test_cornell_cues_errors(client, learner_headers):
    res = client.post(
        "/api/v1/learning/cornell-cues",
        headers=learner_headers,
        json={"kpId": "no_such_kp", "difficulty": "初级"},
    )
    assert res.status_code == 404 and res.json()["code"] == 1004
    res = client.post(
        "/api/v1/learning/cornell-cues",
        headers=learner_headers,
        json={"kpId": "nn", "difficulty": "地狱"},
    )
    assert res.status_code == 400 and res.json()["code"] == 1001


def test_cornell_cues_deepseek_mode_consistent(client, learner_headers, monkeypatch):
    """真实模式结构与 mock 一致；解析失败回落主题模板（线索始终可用）。"""
    def good_chat(prompt, system=None, history=None):
        return json.dumps({
            "cues": [
                {"type": "question", "text": "问题一？"},
                {"type": "question", "text": "问题二？"},
                {"type": "keyword", "text": "关键词"},
                {"type": "question", "text": "问题三？"},
                {"type": "question", "text": "问题四？"},
            ],
            "noteOutline": [{"heading": "要点", "points": ["p1", "p2"]}],
            "summaryHint": "总结引导",
        }, ensure_ascii=False)

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm_deepseek, "chat", good_chat)
    llm_mod._client = None
    try:
        data = client.post(
            "/api/v1/learning/cornell-cues", headers=learner_headers, json={"kpId": "ml"}
        ).json()["data"]
        assert set(data.keys()) == CORNELL_KEYS
        assert 5 <= len(data["cues"]) <= 8

        # 非 JSON 输出 → 回落确定性主题模板（仍是合法康奈尔结构）
        monkeypatch.setattr(llm_deepseek, "chat", lambda *a, **k: "不是 JSON")
        llm_mod._client = None
        data2 = client.post(
            "/api/v1/learning/cornell-cues", headers=learner_headers, json={"kpId": "ml"}
        ).json()["data"]
        assert 5 <= len(data2["cues"]) <= 8
    finally:
        monkeypatch.setattr(settings, "llm_provider", "mock")
        llm_mod._client = None


# ---- 18.2 费曼讲解评估 --------------------------------------------------------

def test_feynman_json_gaps_point_to_real_resources(client, learner_headers):
    # 讲解遗漏「激活函数」→ 识别 high 缺口，review 指向真实资源端点
    res = client.post(
        "/api/v1/learning/feynman",
        headers=learner_headers,
        json={"kpId": "nn", "explanation": "神经网络就是把输入乘以权重加起来得到输出。"},
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert set(data.keys()) == FEYNMAN_JSON_KEYS
    assert data["sessionId"].startswith("f_")
    assert isinstance(data["score"], int) and 0 <= data["score"] <= 100
    assert data["gaps"], "应识别出讲漏的知识缺口"
    top = data["gaps"][0]
    assert set(top.keys()) == {"id", "kpId", "title", "detail", "severity", "review"}
    assert top["severity"] == "high" and "激活函数" in top["title"]
    # review 复用 6.3 resources[]：指向真实资源端点，可点开
    endpoints = {r["endpoint"] for r in top["review"]}
    assert "/resource/lecture" in endpoints and "/resource/diagram" in endpoints
    assert all(r["kpId"] == "nn" for r in top["review"])
    assert data["complete"] is False  # 含 high 缺口 → 未讲充分
    assert data["followups"]  # 引导补讲追问


def test_feynman_complete_when_thorough(client, learner_headers):
    res = client.post(
        "/api/v1/learning/feynman",
        headers=learner_headers,
        json={
            "kpId": "nn",
            "explanation": "神经元先把输入和权重加权求和，再加上偏置b，"
            "然后经过激活函数引入非线性，最后通过反向传播计算梯度来更新权重。",
        },
    )
    data = res.json()["data"]
    assert data["gaps"] == []  # 关键概念全覆盖
    assert data["complete"] is True and data["score"] == 100


def test_feynman_session_reuse(client, learner_headers):
    r1 = client.post(
        "/api/v1/learning/feynman",
        headers=learner_headers,
        json={"kpId": "nn", "explanation": "加权求和后加偏置。"},
    ).json()["data"]
    sid = r1["sessionId"]
    client.post(
        "/api/v1/learning/feynman",
        headers=learner_headers,
        json={"kpId": "nn", "sessionId": sid, "explanation": "再经过激活函数。"},
    )
    assert len(learning_service._sessions[sid]["history"]) == 4  # 2 轮 user+assistant


def test_feynman_unknown_kp_404(client, learner_headers):
    res = client.post(
        "/api/v1/learning/feynman",
        headers=learner_headers,
        json={"kpId": "no_such_kp", "explanation": "随便讲讲。"},
    )
    assert res.status_code == 404 and res.json()["code"] == 1004


def test_feynman_sse_stream(client, learner_headers):
    res = client.post(
        "/api/v1/learning/feynman",
        headers={**learner_headers, "Accept": "text/event-stream"},
        json={"kpId": "nn", "explanation": "神经网络就是把输入乘权重加起来。"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    deltas, named = _parse_sse(res.text)
    assert deltas and all(set(d.keys()) == {"delta"} for d in deltas)
    # event: gaps 携带 Gap[]，review 指向真实端点
    assert "gaps" in named and named["gaps"]["gaps"]
    gap = named["gaps"]["gaps"][0]
    assert any(r["endpoint"] == "/resource/lecture" for r in gap["review"])
    # event: done 携带 score/followups/complete
    done = named["done"]
    assert set(done.keys()) == {"sessionId", "score", "followups", "complete", "generationMeta"}
    assert done["sessionId"].startswith("f_")


def test_feynman_deepseek_mode(client, learner_headers, monkeypatch):
    def fake_chat(prompt, system=None, history=None):
        assert "费曼" in (system or "")  # prompt 改为费曼学习法
        return json.dumps({
            "feedback": "你的讲解抓住了主线，但遗漏了关键一步。",
            "score": 70,
            "gaps": [{"title": "遗漏激活函数", "detail": "未提到非线性。", "severity": "high"}],
            "followups": ["去掉激活函数会怎样？"],
            "complete": False,
        }, ensure_ascii=False)

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm_deepseek, "chat", fake_chat)
    llm_mod._client = None
    try:
        data = client.post(
            "/api/v1/learning/feynman",
            headers=learner_headers,
            json={"kpId": "nn", "explanation": "讲解内容"},
        ).json()["data"]
        assert set(data.keys()) == FEYNMAN_JSON_KEYS  # 与 mock 结构一致
        assert data["gaps"][0]["kpId"] == "nn"  # kpId 回正为当前知识点
        assert data["gaps"][0]["review"]  # 服务层注入 review
        assert data["complete"] is False
    finally:
        monkeypatch.setattr(settings, "llm_provider", "mock")
        llm_mod._client = None


# ---- 18.3 学习步骤进度 --------------------------------------------------------

def test_steps_read_write(client, fresh_headers):
    res = client.get("/api/v1/learning/steps/nn", headers=fresh_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert set(data.keys()) == STEPS_RESP_KEYS
    assert set(data["steps"].keys()) == STEP_KEYS
    assert data["total"] == 6
    assert all(v is False for v in data["steps"].values())
    assert data["completedCount"] == 0

    # 置 feynman 步骤完成
    res = client.post(
        "/api/v1/learning/steps/nn", headers=fresh_headers, json={"step": "feynman", "done": True}
    )
    data = res.json()["data"]
    assert data["steps"]["feynman"] is True and data["completedCount"] == 1
    assert data["updatedAt"]
    # 幂等取消
    res = client.post(
        "/api/v1/learning/steps/nn", headers=fresh_headers, json={"step": "feynman", "done": False}
    )
    assert res.json()["data"]["steps"]["feynman"] is False


def test_steps_invalid_step_and_unknown_kp(client, fresh_headers):
    res = client.post(
        "/api/v1/learning/steps/nn", headers=fresh_headers, json={"step": "homework", "done": True}
    )
    assert res.status_code == 400 and res.json()["code"] == 1001
    res = client.get("/api/v1/learning/steps/no_such_kp", headers=fresh_headers)
    assert res.status_code == 404 and res.json()["code"] == 1004


# ---- 18.4 / 18.5 康奈尔笔记 ---------------------------------------------------

def test_notes_read_write(client, fresh_headers):
    # 初次读取 → 空笔记占位（非错误）
    res = client.get("/api/v1/learning/notes/nn", headers=fresh_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert set(data.keys()) == NOTE_KEYS
    assert data["cueNotes"] == [] and data["mainNotes"] == "" and data["summary"] == ""

    # 保存三栏笔记
    res = client.put(
        "/api/v1/learning/notes/nn",
        headers=fresh_headers,
        json={
            "cueNotes": [{"cueId": "c1", "text": "为引入非线性"}],
            "mainNotes": "## 激活函数\n- 引入非线性",
            "summary": "神经网络通过加权与激活拟合复杂函数。",
        },
    )
    saved = res.json()["data"]
    assert saved["cueNotes"] == [{"cueId": "c1", "text": "为引入非线性"}]
    assert saved["mainNotes"].startswith("## 激活函数")
    assert saved["summary"].startswith("神经网络")
    # 回读一致（整体覆盖持久化）
    again = client.get("/api/v1/learning/notes/nn", headers=fresh_headers).json()["data"]
    assert again["mainNotes"] == saved["mainNotes"]
    assert again["summary"] == saved["summary"]


def test_notes_unknown_kp_404(client, fresh_headers):
    res = client.get("/api/v1/learning/notes/no_such_kp", headers=fresh_headers)
    assert res.status_code == 404 and res.json()["code"] == 1004


# ---- 18.6 掌握度对接：阶段测试通过 → Mastery 真实 passed（复用 9.1 → 7.3）--------

def test_quiz_pass_drives_mastery_passed_in_steps(client, fresh_headers):
    # 初始：steps.mastery 默认 learning（透传 7.1，未掌握）
    before = client.get("/api/v1/learning/steps/nn", headers=fresh_headers).json()["data"]
    assert before["mastery"] != "passed"

    # 复用既有 9.1 测验提交：客观全对 + 简答按参考要点作答 → 综合 ≥60 → masteryUpdated:passed
    # （C-fix 批2：nn 扩到 10 题，含末尾 1 道简答题 nn_q10）
    submit = client.post(
        "/api/v1/quiz/nn/submit",
        headers=fresh_headers,
        json={"answers": [
            {"question_id": "nn_q1", "answer": "b"},
            {"question_id": "nn_q2", "answer": ["a", "b", "d"]},
            {"question_id": "nn_q3", "answer": "true"},
            {"question_id": "nn_q4", "answer": "a"},
            {"question_id": "nn_q5", "answer": "b"},
            {"question_id": "nn_q6", "answer": "true"},
            {"question_id": "nn_q7", "answer": ["a", "b", "c"]},
            {"question_id": "nn_q8", "answer": "b"},
            {"question_id": "nn_q9", "answer": "false"},
            {"question_id": "nn_q10", "answer": "前向传播由输入逐层计算得到预测输出并计算损失；反向传播利用链式法则反向逐层计算梯度用于更新参数；二者交替迭代完成训练。"},
        ]},
    ).json()["data"]
    assert submit["passed"] is True
    assert submit["masteryUpdated"] == {"id": "nn", "status": "passed"}

    # steps.mastery 透传 7.1 → 真实 passed（"已掌握" 以此为准，非 practice 步骤）
    after = client.get("/api/v1/learning/steps/nn", headers=fresh_headers).json()["data"]
    assert after["mastery"] == "passed"
    # 仍未勾选 practice 步骤，印证「步骤完成 ≠ 已掌握」解耦
    assert after["steps"]["practice"] is False
