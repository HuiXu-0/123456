"""B7-a 契约测试：Workflow 实时通道 + SocraticTutor SSE + Video。

验收口径（执行方案 B7 前半 + 接口文档 11 章 / 8.7 / 15.4 / 8.3）：
- POST /workflow/execute 后台运行立即返回 workflowId；GET /workflow/{id} 轮询
  响应结构严格按 11.2（workflowId/phase/step/agents[]/messages[]/stats，无多余键）；
- WS /api/v1/ws/workflow/{id}：连接先推一帧全量快照，节点推进时推同结构增量帧
  （messages 仅含新增），全部套统一信封 {code,message,data,traceId}；
- 强制 critic 低分（B5-a 测试钩子）→ WS 帧序列出现 error 类型消息与重试帧；
- 注册表失效后 GET 兜底消费 WorkflowTrace 行（11.2 渲染结构持久化，B5-a 约定）；
- tutor：JSON 模式按 8.7；Accept: text/event-stream 时按 15.4 流式
  （data:{"delta":..} 逐条 + event: done 收尾）；会话上下文内存 TTL 维持；
  deepseek 模式真实流式且 system prompt 约束「引导式提问不直接给答案」；
- video：videoUrl:null + 5 段帧-旁白映射（帧位与前端 LectureVideo 场景对齐）
  + fps/width/height/durationInFrames。
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.core import llm as llm_mod
from app.core import llm_deepseek
from app.core.config import settings
from app.core.llm import set_force_critic_low
from app.main import app
from app.services import tutor as tutor_service
from app.services import workflow_runner

AGENT_IDS = ["diagnosis", "generation", "critic"]
# 11.2 响应 data 的键集合（严格：不得多键、不得少键）
SNAPSHOT_KEYS = {"workflowId", "phase", "step", "agents", "messages", "stats"}
ENVELOPE_KEYS = {"code", "message", "data", "traceId"}
PHASES = {"idle", "diagnosis", "generation", "validation", "complete"}


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


@pytest.fixture(autouse=True)
def _fast_and_clean():
    """测试默认零演示延迟；每条用例后复位钩子与延迟。"""
    wf, tutor_delay = settings.workflow_step_delay_ms, settings.tutor_stream_delay_ms
    settings.workflow_step_delay_ms = 0
    settings.tutor_stream_delay_ms = 0
    yield
    settings.workflow_step_delay_ms = wf
    settings.tutor_stream_delay_ms = tutor_delay
    set_force_critic_low(False)


def _execute(client, headers, body: dict | None = None) -> str:
    res = client.post("/api/v1/workflow/execute", headers=headers, json=body or {})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["code"] == 0
    wid = payload["data"]["workflowId"]
    assert wid.startswith("wf_")
    return wid


def _poll_until_complete(client, headers, wid: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/api/v1/workflow/{wid}", headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert set(data.keys()) == SNAPSHOT_KEYS  # 每帧严格 11.2 结构
        assert data["phase"] in PHASES
        if data["phase"] == "complete":
            return data
        time.sleep(0.05)
    pytest.fail("工作流未在期限内完成")


def _assert_snapshot_contract(data: dict, wid: str) -> None:
    """11.2 完成态快照断言。"""
    assert set(data.keys()) == SNAPSHOT_KEYS
    assert data["workflowId"] == wid
    assert data["phase"] == "complete" and data["step"] == 4
    agents = data["agents"]
    assert [a["id"] for a in agents] == AGENT_IDS
    for a in agents:
        assert set(a.keys()) == {"id", "name", "status", "lastAction"}
        assert a["status"] in ("idle", "running", "success", "error")
        assert a["name"] and a["lastAction"]
    messages = data["messages"]
    assert [m["id"] for m in messages] == list(range(1, len(messages) + 1))
    for m in messages:
        assert set(m.keys()) == {"id", "from", "to", "message", "type", "timestamp"}
        assert m["type"] in ("request", "response", "error")
    stats = data["stats"]
    assert set(stats.keys()) == {"completedAgents", "messageCount", "progress"}
    assert stats["messageCount"] == len(messages)
    assert stats["progress"] == 100


# ---- 11.1 / 11.2：execute → 轮询 ----------------------------------------------

def test_workflow_execute_and_poll_contract(client, learner_headers):
    wid = _execute(client, learner_headers, {"targetJobId": "llm-app", "kpId": "nn"})
    data = _poll_until_complete(client, learner_headers, wid)
    _assert_snapshot_contract(data, wid)
    # happy path：3 Agent 全 success
    assert all(a["status"] == "success" for a in data["agents"])
    assert data["stats"]["completedAgents"] == 3


def test_workflow_execute_defaults_and_auth(client, learner_headers):
    # 请求体两字段均可选（11.1）
    wid = _execute(client, learner_headers, {})
    _poll_until_complete(client, learner_headers, wid)
    # 未认证 → 1002
    res = client.post("/api/v1/workflow/execute", json={})
    assert res.status_code == 401 and res.json()["code"] == 1002


def test_workflow_get_unknown_404(client, learner_headers):
    res = client.get("/api/v1/workflow/wf_not_exists", headers=learner_headers)
    assert res.status_code == 404
    assert res.json()["code"] == 1004


def test_workflow_poll_falls_back_to_trace_row(client, learner_headers):
    """内存注册表失效后，GET 消费持久化的 WorkflowTrace 行（B5-a → B7 约定）。"""
    wid = _execute(client, learner_headers, {"kpId": "nn"})
    _poll_until_complete(client, learner_headers, wid)
    workflow_runner._runs.pop(wid, None)  # 模拟重启/淘汰
    res = client.get(f"/api/v1/workflow/{wid}", headers=learner_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    _assert_snapshot_contract(data, wid)


# ---- 11.2 WS：全量快照帧 + 同结构增量帧 ----------------------------------------

def _collect_ws_frames(client, wid: str) -> list[dict]:
    """收帧直到 complete；逐帧断言信封 + 11.2 结构。"""
    frames: list[dict] = []
    with client.websocket_connect(f"/api/v1/ws/workflow/{wid}") as ws:
        while True:
            frame = ws.receive_json()
            assert set(frame.keys()) == ENVELOPE_KEYS  # WS 消息同样套信封
            assert frame["code"] == 0
            data = frame["data"]
            assert set(data.keys()) == SNAPSHOT_KEYS
            frames.append(data)
            if data["phase"] == "complete":
                break
    return frames


def _cumulative_messages(frames: list[dict]) -> list[dict]:
    """首帧全量 + 后续增量帧拼接出完整消息流（id 不得重复）。"""
    all_msgs: list[dict] = []
    for f in frames:
        all_msgs.extend(f["messages"])
    ids = [m["id"] for m in all_msgs]
    assert len(ids) == len(set(ids)), "增量帧出现重复消息"
    assert ids == sorted(ids)
    return all_msgs


def test_workflow_ws_snapshot_then_incremental(client, learner_headers):
    settings.workflow_step_delay_ms = 120
    wid = _execute(client, learner_headers, {"kpId": "nn"})
    frames = _collect_ws_frames(client, wid)
    assert len(frames) >= 2, "应至少收到 全量快照帧 + 增量帧"
    msgs = _cumulative_messages(frames)
    final = frames[-1]
    assert final["stats"]["messageCount"] == len(msgs)
    assert final["stats"]["progress"] == 100
    assert all(a["status"] == "success" for a in final["agents"])
    # 帧序列体现 phase 推进（至少出现一个运行中 phase）
    assert any(f["phase"] in ("diagnosis", "generation", "validation") for f in frames)


def test_workflow_ws_forced_low_score_error_and_retry_frames(client, learner_headers):
    settings.workflow_step_delay_ms = 120
    set_force_critic_low(True)
    wid = _execute(client, learner_headers, {"kpId": "transformer"})
    frames = _collect_ws_frames(client, wid)
    msgs = _cumulative_messages(frames)
    # error 类型消息（校验未通过/降级）
    assert any(m["type"] == "error" for m in msgs)
    # 重试消息：第 1 次 + 第 2 次
    joined = "".join(m["message"] for m in msgs)
    assert "第 1 次重试" in joined and "第 2 次重试" in joined
    # 重试帧：发生 error 后 generation 重新 running
    seen_error = False
    saw_retry_frame = False
    for f in frames:
        if any(m["type"] == "error" for m in f["messages"]):
            seen_error = True
        gen = next(a for a in f["agents"] if a["id"] == "generation")
        if seen_error and gen["status"] == "running":
            saw_retry_frame = True
    assert saw_retry_frame, "WS 帧序列未观察到重试帧（error 后 generation 再次 running）"
    # 降级收尾：critic 红灯
    critic = next(a for a in frames[-1]["agents"] if a["id"] == "critic")
    assert critic["status"] == "error"


def test_workflow_ws_unknown_id_envelope_1004(client):
    with client.websocket_connect("/api/v1/ws/workflow/wf_not_exists") as ws:
        frame = ws.receive_json()
        assert set(frame.keys()) == ENVELOPE_KEYS
        assert frame["code"] == 1004


# ---- 8.7 SocraticTutor：JSON 模式 ---------------------------------------------

def test_tutor_json_mode_and_session_reuse(client, learner_headers):
    res = client.post(
        "/api/v1/resource/tutor/chat",
        headers=learner_headers,
        json={"kpId": "nn", "message": "为什么需要激活函数？"},
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert set(data.keys()) == {"sessionId", "reply", "suggestions", "generationMeta"}
    sid = data["sessionId"]
    assert sid.startswith("s_")
    assert data["reply"] and isinstance(data["suggestions"], list)

    # 二轮带 sessionId：会话保持（同 id 回显，历史累积）
    res2 = client.post(
        "/api/v1/resource/tutor/chat",
        headers=learner_headers,
        json={"kpId": "nn", "sessionId": sid, "message": "等价于线性变换"},
    )
    data2 = res2.json()["data"]
    assert data2["sessionId"] == sid
    assert len(tutor_service._sessions[sid]["history"]) == 4  # 2 轮 user+assistant


def test_tutor_unknown_kp_404(client, learner_headers):
    res = client.post(
        "/api/v1/resource/tutor/chat",
        headers=learner_headers,
        json={"kpId": "no_such_kp", "message": "你好"},
    )
    assert res.status_code == 404 and res.json()["code"] == 1004


def test_tutor_session_ttl_expiry_resets_context(client, learner_headers):
    res = client.post(
        "/api/v1/resource/tutor/chat",
        headers=learner_headers,
        json={"kpId": "nn", "message": "先加权求和"},
    )
    sid = res.json()["data"]["sessionId"]
    assert len(tutor_service._sessions[sid]["history"]) == 2
    # 人为过期 → 同 sessionId 再聊，上下文清零重建（TTL 30 分钟语义）
    tutor_service._sessions[sid]["expiresAt"] = 0.0
    client.post(
        "/api/v1/resource/tutor/chat",
        headers=learner_headers,
        json={"kpId": "nn", "sessionId": sid, "message": "加上偏置 b"},
    )
    assert len(tutor_service._sessions[sid]["history"]) == 2  # 仅本轮，旧上下文已失效


# ---- 15.4 SSE 流式（mock：确定性引导链逐字流式） --------------------------------

def _parse_sse(text: str) -> tuple[list[dict], dict[str, dict]]:
    """解析 SSE 文本 → (默认事件 data 列表, {事件名: data})。"""
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
        if event is None:
            deltas.append(payload)
        else:
            named[event] = payload
    return deltas, named


def test_tutor_sse_mock_stream(client, learner_headers):
    res = client.post(
        "/api/v1/resource/tutor/chat",
        headers={**learner_headers, "Accept": "text/event-stream"},
        json={"kpId": "nn", "message": "为什么需要激活函数？"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    deltas, named = _parse_sse(res.text)
    # 逐字流式：delta 事件数 ≥ 回复字数级别
    assert len(deltas) >= 10
    assert all(set(d.keys()) == {"delta"} for d in deltas)
    reply = "".join(d["delta"] for d in deltas)
    assert reply  # 非空回复
    # done 收尾：携带 sessionId + suggestions（15.4）
    done = named["done"]
    assert set(done.keys()) == {"sessionId", "suggestions", "generationMeta"}
    assert done["sessionId"].startswith("s_")
    assert isinstance(done["suggestions"], list) and done["suggestions"]
    # 引导式：不直接给答案（mock 引导链以问题收尾）
    assert "？" in reply
    # 流式回复与 JSON 模式同链路：会话历史已落盘
    sid = done["sessionId"]
    history = tutor_service._sessions[sid]["history"]
    assert history[-1]["content"] == reply


# ---- 15.4 SSE 流式（deepseek：真实流式通道 + prompt 约束） ----------------------

def test_tutor_sse_deepseek_stream(client, learner_headers, monkeypatch):
    chunks = ["你觉得", "如果没有激活函数，", "多层叠加等价于什么？"]
    captured: dict = {}

    def fake_stream(prompt, system=None, history=None):
        captured["prompt"] = prompt
        captured["system"] = system
        captured["history"] = history
        yield from chunks

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm_deepseek, "chat_stream", fake_stream)
    llm_mod._client = None
    try:
        res = client.post(
            "/api/v1/resource/tutor/chat",
            headers={**learner_headers, "Accept": "text/event-stream"},
            json={"kpId": "nn", "message": "为什么需要激活函数？"},
        )
        deltas, named = _parse_sse(res.text)
        assert [d["delta"] for d in deltas] == chunks  # 真实流式：逐 chunk 透传
        assert named["done"]["suggestions"] == []  # deepseek 模式 suggestions 可空
        # prompt 约束：引导式提问、不直接给答案
        assert "引导" in captured["system"]
        assert "不直接给" in captured["system"] or "不要直接给" in captured["system"]
        assert captured["prompt"] == "为什么需要激活函数？"
    finally:
        llm_mod._client = None


def test_tutor_sse_deepseek_error_event(client, learner_headers, monkeypatch):
    def boom_stream(prompt, system=None, history=None):
        raise llm_deepseek.LLMGenerationError("DEEPSEEK_API_KEY 未配置")
        yield  # pragma: no cover

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm_deepseek, "chat_stream", boom_stream)
    llm_mod._client = None
    try:
        res = client.post(
            "/api/v1/resource/tutor/chat",
            headers={**learner_headers, "Accept": "text/event-stream"},
            json={"kpId": "nn", "message": "你好"},
        )
        _deltas, named = _parse_sse(res.text)
        assert named["error"]["code"] == 2001  # 15.4：event: error
    finally:
        llm_mod._client = None


# ---- 8.3 Video：videoUrl:null + 分镜脚本 scenes + 帧-旁白映射 -------------------

def test_video_contract_aligned_with_lecture_video(client, learner_headers):
    res = client.post(
        "/api/v1/resource/video",
        headers=learner_headers,
        json={"kpId": "nn", "difficulty": "初级"},
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert set(data.keys()) == {
        "videoUrl", "title", "scenes", "narration",
        "fps", "width", "height", "durationInFrames",
        "generationMeta",
    }
    assert data["videoUrl"] is None  # 走前端 Remotion Player + TTS
    assert data["title"] == "神经网络基础"
    # 分镜脚本：3-5 个场景，每场景 title/points/narration；nn 为 5 场景
    scenes = data["scenes"]
    assert 3 <= len(scenes) <= 5
    assert len(scenes) == 5
    for i, s in enumerate(scenes):
        assert set(s.keys()) == {"frame", "title", "points", "narration"}
        assert s["frame"] == i * 180  # 场景 i 起始帧 = i × SCENE_FRAMES(180)
        assert s["title"] and s["narration"]
        assert s["points"] and all(p for p in s["points"])
    # narration[] 帧-旁白映射与 scenes 对齐（向后兼容字段）
    narration = data["narration"]
    assert [n["frame"] for n in narration] == [0, 180, 360, 540, 720]
    assert all(set(n.keys()) == {"frame", "text"} and n["text"] for n in narration)
    # nn 旁白与前端 LectureVideo 原 NARRATION 逐字一致（不回归）
    assert narration[0]["text"].startswith("欢迎学习神经网络基础")
    # 视频参数与前端 Remotion 组件一致；总时长 = 场景数 × 180
    assert (data["fps"], data["width"], data["height"], data["durationInFrames"]) == (
        30, 1280, 720, 900,
    )


def test_video_other_kp_and_errors(client, learner_headers):
    res = client.post(
        "/api/v1/resource/video",
        headers=learner_headers,
        json={"kpId": "transformer", "difficulty": "高级"},
    )
    data = res.json()["data"]
    assert len(data["scenes"]) == 5 and data["videoUrl"] is None
    # 画面随知识点动态生成：标题/旁白对应 Transformer，绝不是神经网络
    assert "Transformer" in data["title"]
    assert "神经网络" not in data["scenes"][0]["title"]
    assert "Transformer" in data["narration"][0]["text"]

    res = client.post(
        "/api/v1/resource/video",
        headers=learner_headers,
        json={"kpId": "no_such_kp", "difficulty": "初级"},
    )
    assert res.status_code == 404 and res.json()["code"] == 1004

    res = client.post(
        "/api/v1/resource/video",
        headers=learner_headers,
        json={"kpId": "nn", "difficulty": "地狱"},
    )
    assert res.status_code == 400 and res.json()["code"] == 1001


# ---- 8.3 增量：服务端渲染 mp4（CC-video-mp4 第二步）-------------------------------

def test_video_render_degrades_when_unavailable(client, learner_headers):
    """渲染关闭（测试基线 video_render_enabled=False）→ 回 unavailable，前端回落实时 Player+TTS。

    契约 { status, taskId, videoUrl }；降级时 status=unavailable、taskId/videoUrl 均 null。
    既验证降级纪律（拿不到 mp4 仍可看），也保证测试零网络/零子进程。
    """
    res = client.post(
        "/api/v1/resource/video/render",
        headers=learner_headers,
        json={"kpId": "nn", "difficulty": "初级"},
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert set(data.keys()) == {"status", "taskId", "videoUrl"}
    assert data["status"] == "unavailable"
    assert data["taskId"] is None and data["videoUrl"] is None


def test_video_render_errors(client, learner_headers):
    """渲染触发同样校验 kp/难度：未知 kp → 1004；非法难度 → 1001。"""
    res = client.post(
        "/api/v1/resource/video/render",
        headers=learner_headers,
        json={"kpId": "no_such_kp", "difficulty": "初级"},
    )
    assert res.status_code == 404 and res.json()["code"] == 1004
    res = client.post(
        "/api/v1/resource/video/render",
        headers=learner_headers,
        json={"kpId": "nn", "difficulty": "地狱"},
    )
    assert res.status_code == 400 and res.json()["code"] == 1001


# ---- 8.5 知识图解（issue#7：经 LLMClient 真实生成，mock/deepseek 双模式） --------

def test_diagram_per_topic_via_llm(client, learner_headers):
    """知识图解经 LLMClient 按主题生成：mock 下不同知识点产出不同 Mermaid，恒以 flowchart 开头。"""
    nn = client.get("/api/v1/resource/diagram/nn", headers=learner_headers).json()["data"]
    cnn = client.get("/api/v1/resource/diagram/cnn", headers=learner_headers).json()["data"]
    assert set(nn.keys()) == {"mermaid", "generationMeta"}
    assert set(cnn.keys()) == {"mermaid", "generationMeta"}
    assert nn["mermaid"].startswith("flowchart")
    assert cnn["mermaid"].startswith("flowchart")
    assert nn["mermaid"] != cnn["mermaid"]  # 按主题真实生成，绝非写死一张
    # 未知知识点 → 1004
    res = client.get("/api/v1/resource/diagram/no_such_kp", headers=learner_headers)
    assert res.status_code == 404 and res.json()["code"] == 1004


def test_diagram_deepseek_generate_and_fallback(client, learner_headers, monkeypatch):
    """deepseek 真实生成 Mermaid + 契约清洗；非法输出回落确定性主题模板（图解始终可渲染）。"""
    # 合法 Mermaid（带 ``` 围栏）→ 清洗后采用
    def good_chat(prompt, system=None, history=None):
        return "```mermaid\nflowchart TD\n  A[\"题目\"] --> B[\"解析\"]\n```"

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm_deepseek, "chat", good_chat)
    llm_mod._client = None
    try:
        data = client.get("/api/v1/resource/diagram/ml", headers=learner_headers).json()["data"]
        assert data["mermaid"].startswith("flowchart TD")
        assert "```" not in data["mermaid"]  # 围栏被清洗
    finally:
        monkeypatch.setattr(settings, "llm_provider", "mock")
        llm_mod._client = None

    # 非法输出（非 flowchart）→ 回落主题模板（仍以 flowchart 开头）
    def bad_chat(prompt, system=None, history=None):
        return "这是一段普通文字，不是 Mermaid"

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm_deepseek, "chat", bad_chat)
    llm_mod._client = None
    try:
        data = client.get("/api/v1/resource/diagram/dl", headers=learner_headers).json()["data"]
        assert data["mermaid"].startswith("flowchart")  # 回落兜底
    finally:
        monkeypatch.setattr(settings, "llm_provider", "mock")
        llm_mod._client = None
