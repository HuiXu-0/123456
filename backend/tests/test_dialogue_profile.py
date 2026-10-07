"""C2 契约测试：三段式画像诊断（接口文档 17.1 / 17.2 / 17.3）。

C2 重构验收口径（能力靠测、偏好归类型、主观靠对话）：
- ① 开场段：自由文本采集主观维（learning_goal / prior_experience）；
- ② 微测段：逐题抛出客观微测（event/interaction.type=quiz，复用 quiz 题库），按作答
  **行为反推能力**——答得好 vs 答得差 → knowledge_base 分数显著不同、且带依据 basis；
  **空作答 → 未测 / 低置信、无 score、不臆造**；
- ③ 偏好段：抛出「二/三选一」（interaction.type=preference），按选择**归类型、不打分**；
- 三段走完才 diagnosisComplete=true；能力雷达（4.4）由微测写入的 Mastery 分驱动；
- dashboard 雷达只含能力维（知识点），偏好以类型标签呈现、不上 0-100 轴；
- 两用户对比：A(答得好+图像型) vs B(零基础+案例型) → 能力 / 偏好画像都明显不同；
- SSE：delta → event: portrait → event: interaction → event: done。
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import llm as llm_mod
from app.core import llm_deepseek
from app.core.config import settings
from app.main import app
from app.services import profile_dialogue as dialogue_service

DIALOGUE_KEYS = {
    "sessionId",
    "reply",
    "portraitUpdates",
    "suggestions",
    "diagnosisComplete",
    "interaction",
    "generationMeta",
}
PORTRAIT_KEYS = {"dimensions", "version", "updatedAt"}
DIM_KEYS_REQUIRED = {"key", "label", "kind", "value", "confidence", "source"}
DIM_KEYS_OPTIONAL = {"score", "basis", "optionKey", "updatedAt"}
SOURCES = {"dialogue", "manual", "inferred", "diagnostic"}
KINDS = {"ability", "preference", "subjective"}
ABILITY_KEYS = {"knowledge_base"}
PREFERENCE_KEYS = {"cognitive_style", "learning_pace", "error_preference"}
SUBJECTIVE_KEYS = {"prior_experience", "learning_goal"}
VALID_DIM_KEYS = ABILITY_KEYS | PREFERENCE_KEYS | SUBJECTIVE_KEYS


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_stream_delay():
    original = settings.tutor_stream_delay_ms
    settings.tutor_stream_delay_ms = 0
    yield
    settings.tutor_stream_delay_ms = original


def _fresh_headers(client: TestClient) -> dict[str, str]:
    username = f"dlg_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/v1/auth/register", json={"username": username, "password": "123456"}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


def _dialogue(client, headers, message="", answer=None, session_id=None, context=None) -> dict:
    body: dict = {"message": message}
    if answer is not None:
        body["answer"] = answer
    if session_id is not None:
        body["sessionId"] = session_id
    if context is not None:
        body["context"] = context
    res = client.post("/api/v1/profile/dialogue", headers=headers, json=body)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["code"] == 0
    return payload["data"]


def _get_portrait(client, headers) -> dict:
    res = client.get("/api/v1/profile/student-portrait", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _assert_dim_contract(dim: dict) -> None:
    assert DIM_KEYS_REQUIRED <= set(dim.keys())
    assert set(dim.keys()) <= DIM_KEYS_REQUIRED | DIM_KEYS_OPTIONAL
    assert dim["key"] in VALID_DIM_KEYS
    assert dim["kind"] in KINDS
    assert isinstance(dim["value"], str) and dim["value"]
    assert dim["source"] in SOURCES
    assert 0.0 <= dim["confidence"] <= 1.0
    # 三分类铁律：score 仅 ability 维可有；偏好/主观维严禁打分（不上 0-100 轴）
    if dim["kind"] != "ability":
        assert "score" not in dim, f"{dim['key']} 非能力维不应带 score"
    if "score" in dim:
        assert dim["kind"] == "ability" and 0 <= dim["score"] <= 100
    # 偏好维须带类型码 optionKey
    if dim["kind"] == "preference":
        assert dim.get("optionKey"), "偏好维须带 optionKey 类型码"


def _correct_answer(client, headers, interaction) -> str:
    """查 quiz 题库取该微测题正确答案（single/boolean 为字符串）。"""
    kp = interaction["meta"]["kpId"]
    qs = client.get(f"/api/v1/quiz/{kp}", headers=headers).json()["data"]["questions"]
    q = next(x for x in qs if x["question_text"] == interaction["prompt"])
    return q["correct_answer"]


def _answer_quiz(client, headers, sid, interaction, correct: bool) -> dict:
    ca = _correct_answer(client, headers, interaction)
    if correct:
        ans = ca
    else:
        ans = next(o["value"] for o in interaction["options"] if o["value"] != ca)
    return _dialogue(client, headers, answer=ans, session_id=sid)


def _run_flow(client, headers, *, correct=True, picks=None, skip_microtest=False) -> tuple[str, dict]:
    """跑完整三段式诊断，返回 (sessionId, 末轮 data)。"""
    picks = picks or []
    d = _dialogue(client, headers, "我做过一些 Python 项目")
    sid = d["sessionId"]
    d = _dialogue(client, headers, "想转大模型应用方向", session_id=sid)
    # ② 微测段
    while d["interaction"] and d["interaction"]["type"] == "quiz":
        if skip_microtest:
            d = _dialogue(client, headers, answer="", session_id=sid)
        else:
            d = _answer_quiz(client, headers, sid, d["interaction"], correct)
    # ③ 偏好段
    i = 0
    while d["interaction"] and d["interaction"]["type"] == "preference":
        keys = [o["value"] for o in d["interaction"]["options"]]
        pick = picks[i] if i < len(picks) and picks[i] in keys else keys[0]
        d = _dialogue(client, headers, answer=pick, session_id=sid)
        i += 1
    return sid, d


# ---- 17.3 空画像 + 17.1 单轮结构 ----------------------------------------------

def test_empty_portrait_before_dialogue(client):
    headers = _fresh_headers(client)
    data = _get_portrait(client, headers)
    assert set(data.keys()) == PORTRAIT_KEYS
    assert data["dimensions"] == []
    assert data["version"] == "v1"
    assert data["updatedAt"]


def test_dialogue_json_contract_keys(client):
    headers = _fresh_headers(client)
    data = _dialogue(client, headers, "我做过 Python 爬虫项目")
    assert set(data.keys()) == DIALOGUE_KEYS
    assert data["sessionId"].startswith("d_")
    assert data["reply"]
    assert isinstance(data["suggestions"], list)
    assert isinstance(data["diagnosisComplete"], bool)
    # 开场首轮抽取主观维（先验经验），interaction 尚为 None（仍在追问学习目标）
    for dim in data["portraitUpdates"]:
        _assert_dim_contract(dim)
        assert dim["kind"] == "subjective"
    assert data["interaction"] is None


# ---- ② 微测：开场后抛出客观题（引导式） ---------------------------------------

def test_opening_transitions_to_microtest(client):
    headers = _fresh_headers(client)
    d = _dialogue(client, headers, "我做过项目")
    sid = d["sessionId"]
    assert d["interaction"] is None  # 开场仍在采集主观维
    d = _dialogue(client, headers, "想转岗求职", session_id=sid)
    # 主观维齐 → 转微测，抛出第一道客观题
    assert d["interaction"] is not None
    assert d["interaction"]["type"] == "quiz"
    assert d["interaction"]["dimKey"] == "knowledge_base"
    assert d["interaction"]["meta"]["kpId"]
    assert len(d["interaction"]["options"]) >= 2
    assert not d["diagnosisComplete"]


# ---- ① 能力靠测不靠说：答得好 vs 答得差 vs 空作答 ------------------------------

def test_ability_is_tested_not_self_reported(client):
    # 答得好 → 高分 + 依据；答得差 → 低分 + 依据；空作答 → 未测/低置信/无 score
    h_good = _fresh_headers(client)
    _run_flow(client, h_good, correct=True)
    kb_good = next(d for d in _get_portrait(client, h_good)["dimensions"] if d["key"] == "knowledge_base")

    h_bad = _fresh_headers(client)
    _run_flow(client, h_bad, correct=False)
    kb_bad = next(d for d in _get_portrait(client, h_bad)["dimensions"] if d["key"] == "knowledge_base")

    h_skip = _fresh_headers(client)
    _run_flow(client, h_skip, skip_microtest=True)
    kb_skip = next(d for d in _get_portrait(client, h_skip)["dimensions"] if d["key"] == "knowledge_base")

    # 能力维均为 ability，且都带依据 basis（可解释、防臆造）
    for kb in (kb_good, kb_bad):
        assert kb["kind"] == "ability"
        assert kb["source"] == "diagnostic"
        assert kb.get("basis")
        assert isinstance(kb["score"], int)
    # 答得好分数显著高于答得差（行为反推，非自陈）
    assert kb_good["score"] > kb_bad["score"]
    assert kb_good["score"] >= 80 and kb_bad["score"] <= 40
    # 空作答：未测 / 低置信 / 无 score（不臆造高分）
    assert "score" not in kb_skip
    assert kb_skip["source"] == "inferred"
    assert kb_skip["confidence"] <= 0.4
    assert "未测" in kb_skip["value"]


def test_microtest_writes_ability_radar_baseline(client):
    """微测写入 Mastery 低置信基线 → 4.4 能力雷达由实测驱动（口径统一）。"""
    h_good = _fresh_headers(client)
    _run_flow(client, h_good, correct=True)
    good = client.get("/api/v1/profile/ability-portrait", headers=h_good).json()["data"]

    h_bad = _fresh_headers(client)
    _run_flow(client, h_bad, correct=False)
    bad = client.get("/api/v1/profile/ability-portrait", headers=h_bad).json()["data"]

    # 4.4 契约：6 个固定能力维名不变；值 0-100
    assert good["dimensions"] == bad["dimensions"]
    assert all(isinstance(v, int) and 0 <= v <= 100 for v in good["values"])
    # 答得好的能力轴整体显著高于答得差（靠测，不再是写死基线）
    assert sum(good["values"]) > sum(bad["values"])


# ---- ③ 偏好归类型不打分 -------------------------------------------------------

def test_preferences_classified_as_type_without_score(client):
    headers = _fresh_headers(client)
    _, done = _run_flow(client, headers, correct=True, picks=["visual", "overview", "concept"])
    assert done["diagnosisComplete"] is True
    dims = {d["key"]: d for d in _get_portrait(client, headers)["dimensions"]}
    for key in PREFERENCE_KEYS:
        pref = dims[key]
        assert pref["kind"] == "preference"
        assert "score" not in pref  # 偏好维严禁打分
        assert pref.get("optionKey")  # 归到某个类型码
    assert dims["cognitive_style"]["optionKey"] == "visual"
    assert dims["learning_pace"]["optionKey"] == "overview"
    assert dims["error_preference"]["optionKey"] == "concept"


# ---- 两用户对比：能力 + 偏好都明显不同 ----------------------------------------

def test_two_user_comparison(client):
    h_a = _fresh_headers(client)
    _run_flow(client, h_a, correct=True, picks=["visual", "overview", "concept"])
    a = {d["key"]: d for d in _get_portrait(client, h_a)["dimensions"]}

    h_b = _fresh_headers(client)
    _run_flow(client, h_b, correct=False, picks=["example", "deepdive", "coding"])
    b = {d["key"]: d for d in _get_portrait(client, h_b)["dimensions"]}

    # 能力画像不同（A 高、B 低）
    assert a["knowledge_base"]["score"] > b["knowledge_base"]["score"]
    # 偏好画像不同（类型码不同）
    assert a["cognitive_style"]["optionKey"] != b["cognitive_style"]["optionKey"]
    assert a["learning_pace"]["optionKey"] != b["learning_pace"]["optionKey"]
    assert a["error_preference"]["optionKey"] != b["error_preference"]["optionKey"]

    # 概览综合分也不同
    ov_a = client.get("/api/v1/dashboard/overview", headers=h_a).json()["data"]
    ov_b = client.get("/api/v1/dashboard/overview", headers=h_b).json()["data"]
    assert ov_a["overall_score"] > ov_b["overall_score"]


# ---- 雷达不混轴：能力雷达 + 偏好类型标签 --------------------------------------

def test_dashboard_ability_radar_and_preference_tags(client):
    headers = _fresh_headers(client)
    _run_flow(client, headers, correct=True, picks=["textual", "deepdive", "calculation"])
    ov = client.get("/api/v1/dashboard/overview", headers=headers).json()["data"]
    # 雷达只含能力维（知识点名），不含任何偏好维标签
    ability_names = client.get("/api/v1/profile/ability-portrait", headers=headers).json()["data"]["dimensions"]
    assert ov["radar"]["dimensions"] == ability_names
    pref_labels = {"认知风格", "学习节奏", "易错点偏好"}
    assert not (set(ov["radar"]["dimensions"]) & pref_labels), "偏好维不得出现在 0-100 雷达轴"
    # 偏好以类型标签呈现、无分数
    assert isinstance(ov["preferences"], list) and ov["preferences"]
    for pref in ov["preferences"]:
        assert set(pref.keys()) == {"key", "label", "value", "optionKey"}
        assert "score" not in pref
        assert pref["value"]  # 类型中文标签


# ---- 收敛：三段走完才完成 -----------------------------------------------------

def test_completes_only_after_three_stages(client):
    headers = _fresh_headers(client)
    d = _dialogue(client, headers, "做过项目")
    sid = d["sessionId"]
    assert not d["diagnosisComplete"]
    d = _dialogue(client, headers, "兴趣自学", session_id=sid)
    assert not d["diagnosisComplete"]  # 刚进微测
    # 微测期间均未完成
    while d["interaction"] and d["interaction"]["type"] == "quiz":
        assert not d["diagnosisComplete"]
        d = _answer_quiz(client, headers, sid, d["interaction"], correct=True)
    # 偏好期间均未完成，最后一题后才完成
    while d["interaction"] and d["interaction"]["type"] == "preference":
        keys = [o["value"] for o in d["interaction"]["options"]]
        d = _dialogue(client, headers, answer=keys[0], session_id=sid)
    assert d["diagnosisComplete"] is True
    # 完成后画像满 6 维（能力1 + 偏好3 + 主观2），每维契约自洽
    dims = _get_portrait(client, headers)["dimensions"]
    assert {d["key"] for d in dims} == VALID_DIM_KEYS
    for dim in dims:
        _assert_dim_contract(dim)


# ---- 17.1 SSE：delta → portrait → interaction → done --------------------------

def _parse_sse(text: str):
    deltas = []
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
        assert data_line is not None
        payload = json.loads(data_line)
        if event is None:
            deltas.append(payload)
        else:
            named[event] = payload
    return deltas, named


def test_dialogue_sse_emits_interaction_on_microtest(client):
    headers = _fresh_headers(client)
    # 开场两轮（JSON）推进到微测前一刻
    d = _dialogue(client, headers, "做过项目")
    sid = d["sessionId"]
    # 第二轮走 SSE：应转入微测并下发 event: interaction
    res = client.post(
        "/api/v1/profile/dialogue",
        headers={**headers, "Accept": "text/event-stream"},
        json={"message": "想转岗求职", "sessionId": sid},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    deltas, named = _parse_sse(res.text)
    assert deltas and all(set(x.keys()) == {"delta"} for x in deltas)
    assert "interaction" in named
    assert named["interaction"]["type"] == "quiz"
    assert "done" in named
    assert set(named["done"].keys()) == {"sessionId", "suggestions", "diagnosisComplete", "generationMeta"}


# ---- 首轮 context（表单显式填写）→ manual ------------------------------------

def test_first_turn_context_is_manual_source(client):
    headers = _fresh_headers(client)
    data = _dialogue(client, headers, "你好", context={"major": "计算机科学", "goal": "职业培训"})
    by_key = {d["key"]: d for d in data["portraitUpdates"]}
    # 目标由表单显式填写 → manual（且为主观维、不打分）
    assert "learning_goal" in by_key
    assert by_key["learning_goal"]["source"] == "manual"
    assert by_key["learning_goal"]["kind"] == "subjective"
    assert "score" not in by_key["learning_goal"]


# ---- PUT 17.4：三条路径统一产出三分类结构 ------------------------------------

def test_replace_portrait_attaches_kind(client):
    headers = _fresh_headers(client)
    # 模拟简历/手动路径：回写一份混合维度（不带 kind）→ 服务端按 key 归类、剥离非法 score
    body = {
        "dimensions": [
            {"key": "knowledge_base", "label": "知识基础", "value": "一般", "score": 60, "source": "manual"},
            {"key": "cognitive_style", "label": "认知风格", "value": "图像型",
             "optionKey": "visual", "score": 99, "source": "manual"},  # 偏好维 score 应被剥离
            {"key": "learning_goal", "label": "学习目标", "value": "求职", "source": "manual"},
        ]
    }
    res = client.put("/api/v1/profile/student-portrait", headers=headers, json=body)
    assert res.status_code == 200, res.text
    dims = {d["key"]: d for d in res.json()["data"]["dimensions"]}
    assert dims["knowledge_base"]["kind"] == "ability" and dims["knowledge_base"]["score"] == 60
    assert dims["cognitive_style"]["kind"] == "preference"
    assert "score" not in dims["cognitive_style"]  # 偏好维 score 被剥离
    assert dims["cognitive_style"]["optionKey"] == "visual"
    assert dims["learning_goal"]["kind"] == "subjective" and "score" not in dims["learning_goal"]


# ---- mock / deepseek 双模式：开场抽取结构一致 + 契约清洗 ----------------------

def test_dialogue_deepseek_opening_structure_and_sanitize(client, monkeypatch):
    headers = _fresh_headers(client)
    fake = {
        "updates": [
            {"key": "prior_experience", "label": "先验经验", "value": "有 NLP 项目经验",
             "confidence": 0.85, "source": "dialogue"},
            {"key": "knowledge_base", "label": "知识基础", "value": "扎实",
             "score": 95, "confidence": 0.9, "source": "dialogue"},  # 能力自陈 → 开场段应丢弃
            {"key": "not_a_real_dim", "label": "x", "value": "y",
             "confidence": 0.9, "source": "dialogue"},  # 非白名单 → 丢弃
        ]
    }

    def fake_chat(prompt, system=None, history=None):
        return json.dumps(fake, ensure_ascii=False)

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm_deepseek, "chat", fake_chat)
    llm_mod._client = None
    try:
        data = _dialogue(client, headers, "我做过 NLP 相关项目")
        assert set(data.keys()) == DIALOGUE_KEYS
        keys = {d["key"] for d in data["portraitUpdates"]}
        assert "not_a_real_dim" not in keys
        # 开场段只采集主观维：能力自陈（knowledge_base）被丢弃，能力只认微测
        assert keys <= SUBJECTIVE_KEYS
        for dim in data["portraitUpdates"]:
            _assert_dim_contract(dim)
    finally:
        monkeypatch.setattr(settings, "llm_provider", "mock")
        llm_mod._client = None


def test_dialogue_deepseek_generation_error_maps_2001(client, monkeypatch):
    headers = _fresh_headers(client)

    def boom(prompt, system=None, history=None):
        raise llm_deepseek.LLMGenerationError("DEEPSEEK_API_KEY 未配置")

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm_deepseek, "chat", boom)
    llm_mod._client = None
    try:
        res = client.post(
            "/api/v1/profile/dialogue", headers=headers, json={"message": "你好"}
        )
        assert res.status_code == 500 and res.json()["code"] == 2001
    finally:
        monkeypatch.setattr(settings, "llm_provider", "mock")
        llm_mod._client = None


# ---- 鉴权 + 会话 TTL -----------------------------------------------------------

def test_dialogue_requires_auth(client):
    res = client.post("/api/v1/profile/dialogue", json={"message": "你好"})
    assert res.status_code == 401 and res.json()["code"] == 1002
    res2 = client.get("/api/v1/profile/student-portrait")
    assert res2.status_code == 401 and res2.json()["code"] == 1002


def test_session_ttl_expiry_resets_context(client):
    headers = _fresh_headers(client)
    d1 = _dialogue(client, headers, "我做过项目")
    sid = d1["sessionId"]
    assert len(dialogue_service._sessions[sid]["history"]) == 2
    dialogue_service._sessions[sid]["expiresAt"] = 0.0
    _dialogue(client, headers, "做过 Python 项目", session_id=sid)
    assert len(dialogue_service._sessions[sid]["history"]) == 2  # 旧上下文失效，仅本轮
