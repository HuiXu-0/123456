"""B8 契约快照测试：30 接口（含 B8 补齐的 19/20）+ 管理端 6 接口逐一断言。

目的：把《后端接口文档》的响应结构（字段名 / 类型 / 枚举值）固化为快照断言，
后续任何改动引起字段漂移（缺字段 / 多字段 / 类型变化 / 枚举越界）即测试失败。

口径：
- 信封：所有接口恰好 {code, message, data, traceId} 四键（1.2）；
- 字段集采用「精确匹配」：required 必须全有，required∪optional 之外的键视为漂移；
- 枚举逐一校验（KPStatus / 难度档 / 题型 / source / phase / agents[].status 等）；
- 全程 mock provider（conftest 基线，确定性、零网络）；工作流/打字机延迟归零提速；
- 测试自带 learner_001 掌握度 + 旅程状态保存/复原，不污染 dev 库；
  上传的知识库测试文档在 #33 删除清理。

按接口文档第 13 章总览表编号组织（test_01 … test_36），文件内顺序执行，
后序用例可复用前序产物（taskId / workflowId / 上传文档 id）。
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import Journey, Mastery, User

API = "/api/v1"

# ---- 契约常量（接口文档 2 章数据字典） ------------------------------------------
DIMENSIONS = ["机器学习基础", "神经网络", "深度学习", "注意力机制", "Transformer", "大模型微调"]
KP_IDS = {"ml", "nn", "dl", "cnn", "transformer", "finetune"}
KP_STATUS = {"learning", "pending-check", "passed"}
PATH_DIFFICULTIES = {"入门", "初级", "中级", "高级", "精通"}
LECTURE_DIFFICULTIES = {"入门", "初级", "中级", "高级", "精通"}
LESSON_STATUS = {"completed", "in_progress", "pending"}
SOURCE_KINDS = {"resume", "ocr", "text", "manual"}
MATERIAL_KINDS = {"doc", "image", "text"}
SOURCE_TYPES = {"教材", "论文", "文档", "课程"}
EXTERNAL_TYPES = {"视频", "论文", "文档", "课程"}
QUESTION_TYPES = {"single", "multiple", "boolean", "short_answer"}
TASK_STATUS = {"pending", "running", "succeeded", "failed"}
WF_PHASES = {"idle", "diagnosis", "generation", "validation", "complete"}
AGENT_STATUS = {"idle", "running", "success", "error"}
MSG_TYPES = {"request", "response", "error"}
JOURNEY_STEPS = {"diagnose", "generate-path", "learn", "review"}
HEAT_ENUM = {"极高", "高", "中"}
GRAPH_NODE_IDS = {
    "ml", "nn", "dl", "cnn", "rnn", "attn",
    "transformer", "bertgpt", "finetune", "prompt", "rag", "agent",
}
DOC_STATUS = {"pending", "indexing", "indexed", "failed"}
AGENT_IDS = {"diagnosis", "generation", "critic"}

# 跨用例共享产物（文件内顺序执行）
shared: dict[str, Any] = {}


# ---- 基础设施 -------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """TestClient（触发 lifespan 建表+种子）；演示延迟归零提速。"""
    original = (
        settings.workflow_step_delay_ms,
        settings.tutor_stream_delay_ms,
        settings.job_market_max_age_hours,
    )
    settings.workflow_step_delay_ms = 0
    settings.tutor_stream_delay_ms = 0
    # 契约快照验证正常态字段；陈旧数据 2002 降级由 TASK-006-G 专项覆盖。
    settings.job_market_max_age_hours = 24 * 365 * 10
    with TestClient(app) as c:
        yield c
    (
        settings.workflow_step_delay_ms,
        settings.tutor_stream_delay_ms,
        settings.job_market_max_age_hours,
    ) = original


@pytest.fixture(scope="module", autouse=True)
def preserve_learner_state(client):
    """保存 learner_001 掌握度与旅程原状，模块结束逐行复原（不污染 dev 库）。"""
    db = SessionLocal()
    user = db.query(User).filter(User.username == "learner_001").one()
    mastery_rows = [
        (m.kp_id, m.status)
        for m in db.query(Mastery).filter(Mastery.user_id == user.id).all()
    ]
    journey = db.get(Journey, user.id)
    journey_state = (
        (journey.has_diagnosed, journey.has_generated_path,
         journey.target_job_name, journey.match_pct)
        if journey is not None
        else None
    )
    yield user.id
    db.query(Mastery).filter(Mastery.user_id == user.id).delete()
    for kp_id, status in mastery_rows:
        db.add(Mastery(user_id=user.id, kp_id=kp_id, status=status))
    if journey_state is not None:
        j = db.get(Journey, user.id)
        (j.has_diagnosed, j.has_generated_path,
         j.target_job_name, j.match_pct) = journey_state
    db.commit()
    db.close()


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    res = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture(scope="module")
def learner(client) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


@pytest.fixture(scope="module")
def admin(client) -> dict[str, str]:
    return _login(client, "admin", "admin123")


def _data(res, *, code: int = 0, status: int = 200) -> Any:
    """断言统一信封（1.2/1.3）并取 data。"""
    assert res.status_code == status, f"HTTP {res.status_code}: {res.text[:300]}"
    body = res.json()
    assert set(body) == {"code", "message", "data", "traceId"}, f"信封漂移：{set(body)}"
    assert body["code"] == code, f"code {body['code']} != {code}: {body['message']}"
    assert isinstance(body["message"], str) and isinstance(body["traceId"], str)
    return body["data"]


def _exact(obj: dict, required: set[str], optional: set[str] = frozenset()) -> None:
    """精确字段集断言：required 全有；required∪optional 之外即漂移。"""
    assert isinstance(obj, dict), f"应为对象，得到 {type(obj).__name__}"
    keys = set(obj)
    missing = required - keys
    extra = keys - required - set(optional)
    assert not missing, f"缺失字段：{sorted(missing)}"
    assert not extra, f"多出字段（漂移）：{sorted(extra)}"


def _wait_task(client: TestClient, headers: dict, task_id: str, timeout: float = 90) -> dict:
    """轮询 GET /tasks/{id} 至终态（15.2）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _data(client.get(f"{API}/tasks/{task_id}", headers=headers))
        if data["status"] in ("succeeded", "failed"):
            return data
        time.sleep(0.1)
    pytest.fail(f"任务 {task_id} 超时未达终态")


def _assert_quiz_question(q: dict) -> None:
    """QuizQuestion 契约（2.5）。"""
    _exact(q, {"question_id", "question_type", "question_text",
               "options", "correct_answer", "explanation"})
    assert q["question_type"] in QUESTION_TYPES
    assert isinstance(q["question_text"], str) and q["question_text"]
    correct = q["correct_answer"]
    if q["question_type"] == "short_answer":
        # 简答题（C-fix 批2）：options 为空，correct_answer 为参考要点列表，
        # explanation 为参考答案（AI 评分见 services.quiz）。
        assert q["options"] == []
        assert isinstance(correct, list) and correct and all(isinstance(p, str) for p in correct)
        assert isinstance(q["explanation"], str)
        return
    assert isinstance(q["options"], list) and len(q["options"]) >= 2
    for opt in q["options"]:
        _exact(opt, {"option_id", "option_text"})
    option_ids = {o["option_id"] for o in q["options"]}
    if q["question_type"] == "multiple":
        assert isinstance(correct, list) and correct
        assert set(correct) <= option_ids
    else:
        assert isinstance(correct, str) and correct in option_ids
    assert isinstance(q["explanation"], str)


def _assert_workflow_snapshot(data: dict) -> None:
    """11.2 严格 6 键快照。"""
    _exact(data, {"workflowId", "phase", "step", "agents", "messages", "stats"})
    assert data["phase"] in WF_PHASES
    assert isinstance(data["step"], int) and 1 <= data["step"] <= 4
    assert [a["id"] for a in data["agents"]] == ["diagnosis", "generation", "critic"]
    for agent in data["agents"]:
        _exact(agent, {"id", "name", "status", "lastAction"})
        assert agent["id"] in AGENT_IDS
        assert agent["status"] in AGENT_STATUS
    for msg in data["messages"]:
        _exact(msg, {"id", "from", "to", "message", "type", "timestamp"})
        assert isinstance(msg["id"], int)
        assert msg["type"] in MSG_TYPES
    _exact(data["stats"], {"completedAgents", "messageCount", "progress"})
    assert 0 <= data["stats"]["progress"] <= 100


# ============================== Auth（1–3） ======================================

def test_01_login(client):
    data = _data(client.post(f"{API}/auth/login",
                             json={"username": "learner_001", "password": "123456"}))
    _exact(data, {"token", "expiresIn", "user"})
    assert isinstance(data["token"], str) and data["token"]
    assert isinstance(data["expiresIn"], int)
    user = data["user"]
    _exact(user, {"userId", "username", "displayName", "role",
                  "hasDiagnosed", "hasGeneratedPath"})
    assert user["role"] in {"learner", "admin"}  # 15.1
    assert isinstance(user["hasDiagnosed"], bool)
    assert isinstance(user["hasGeneratedPath"], bool)


def test_02_forgot_password(client):
    data = _data(client.post(f"{API}/auth/forgot-password", json={"email": "x@x.com"}))
    _exact(data, {"sent"})
    assert data["sent"] is True


def test_03_logout(client):
    headers = _login(client, "learner_001", "123456")  # 独立 token，不影响模块夹具
    data = _data(client.post(f"{API}/auth/logout", headers=headers))
    _exact(data, {"loggedOut"})
    # 失效后再用 → 1002 / 401
    _data(client.get(f"{API}/journey", headers=headers), code=1002, status=401)


# ============================== Profile（4–7） ===================================

def test_04_profile_parse(client, learner):
    res = client.post(f"{API}/profile/parse", headers=learner,
                      data={"description": "三年机器学习与深度学习项目经验"})
    data = _data(res)
    _exact(data, {"education", "major", "goal", "skills", "materials"},
           optional={"generationMeta"})
    for field in ("education", "major", "goal"):
        _exact(data[field], {"value", "source"})
        assert data[field]["source"] in SOURCE_KINDS
    assert [s["name"] for s in data["skills"]] == DIMENSIONS  # 固定 6 维
    for skill in data["skills"]:
        _exact(skill, {"name", "level", "source"})
        assert isinstance(skill["level"], int) and 0 <= skill["level"] <= 100
        assert skill["source"] in SOURCE_KINDS
    for material in data["materials"]:
        _exact(material, {"id", "label", "kind"})
        assert material["kind"] in MATERIAL_KINDS
    shared["parsed"] = data


def test_05_profile_narrative(client, learner):
    parsed = shared["parsed"]
    draft = {k: parsed[k] for k in ("education", "major", "goal", "skills")}
    body = {
        "draft": draft,
        "materials": parsed["materials"],
        "targetJob": {"name": "大模型应用工程师",
                      "radar": {d: 70 for d in DIMENSIONS}},
    }
    data = _data(client.post(f"{API}/profile/narrative", headers=learner, json=body))
    _exact(data, {"paragraphs", "sources", "materialCount"}, optional={"generationMeta"})
    assert len(data["paragraphs"]) == 2  # 恰好两段（4.2）
    for paragraph in data["paragraphs"]:
        assert isinstance(paragraph, list) and paragraph
        for seg in paragraph:
            _exact(seg, {"text"}, optional={"tone", "sourceId"})
            assert isinstance(seg["text"], str)
            if "tone" in seg:
                assert seg["tone"] in {"key", "weak"}
    for source in data["sources"]:
        _exact(source, {"id", "label", "kind"})
    assert data["materialCount"] == len(data["sources"])
    # 无材料 → data null（防幻觉约束，与前端一致）
    empty = _data(client.post(f"{API}/profile/narrative", headers=learner,
                              json={"draft": draft, "materials": [], "targetJob": None}))
    assert empty is None


def test_06_diagnosis_complete(client, learner):
    data = _data(client.post(f"{API}/profile/diagnosis-complete", headers=learner,
                             json={"targetJobName": "大模型应用工程师", "matchPct": 62}))
    _exact(data, {"hasDiagnosed"})
    assert data["hasDiagnosed"] is True


def test_07_ability_portrait(client, learner):
    data = _data(client.get(f"{API}/profile/ability-portrait", headers=learner))
    _exact(data, {"dimensions", "values"})
    assert data["dimensions"] == DIMENSIONS
    assert len(data["values"]) == 6
    assert all(isinstance(v, int) and 0 <= v <= 100 for v in data["values"])


# ============================== Job Market（8–9） ================================

def test_08_job_market_hot(client, learner):
    data = _data(client.get(f"{API}/job-market/hot", headers=learner))
    assert isinstance(data, list) and len(data) == 4
    for job in data:
        _exact(job, {"id", "name"})
    shared["job_id"] = data[0]["id"]


def test_09_job_market_snapshot(client, learner):
    data = _data(client.get(f"{API}/job-market/{shared['job_id']}", headers=learner))
    _exact(data, {"id", "name", "salaryRange", "salaryMedian", "heat", "heatPct",
                  "openings", "source", "fetchedAt", "skills", "radar"})
    assert data["heat"] in HEAT_ENUM
    assert 0 <= data["heatPct"] <= 100
    assert isinstance(data["openings"], int)
    for skill in data["skills"]:
        _exact(skill, {"name", "freqPct"})
    assert set(data["radar"]) == set(DIMENSIONS)  # 雷达 6 维固定键名（2.4）
    # 未知岗位 → 1004
    _data(client.get(f"{API}/job-market/no-such-job", headers=learner),
          code=1004, status=404)


# ============================== Learning Path（10–11） ===========================

def test_10_learning_path(client, learner):
    data = _data(client.get(f"{API}/learning-path", headers=learner))
    _exact(data, {"lessons", "milestones", "summary"})
    # 会话三：路径不再固定 6 步——有目标 → 在 78 点树上聚焦细化到合理长度；sequence 连续。
    assert len(data["lessons"]) >= 6
    assert [ls["sequence"] for ls in data["lessons"]] == list(range(1, len(data["lessons"]) + 1))
    for lesson in data["lessons"]:
        # 六字段契约 + additive（6.3 kpId/reason/resources、会话三 estimatedMinutes）
        _exact(lesson, {"sequence", "topic", "difficulty", "status",
                        "progress", "description"},
               optional={"kpId", "reason", "resources", "estimatedMinutes"})
        assert lesson["difficulty"] in PATH_DIFFICULTIES
        assert lesson["status"] in LESSON_STATUS
        assert 0 <= lesson["progress"] <= 100
    for ms in data["milestones"]:
        _exact(ms, {"id", "title", "completed", "date"})
        assert isinstance(ms["completed"], bool)
        assert ms["date"] is None or isinstance(ms["date"], str)
    # C2 additive：narrative（规划叙述）；会话三 additive：timeline（时间线聚合）
    _exact(data["summary"], {"completedCount", "inProgressCount", "overallProgress"},
           optional={"narrative", "timeline"})


def test_11_learning_path_generate(client, learner):
    data = _data(client.post(f"{API}/learning-path/generate", headers=learner,
                             json={"targetJobId": "llm-app"}))
    _exact(data, {"taskId"})
    shared["path_task_id"] = data["taskId"]


# ============================== Mastery & Journey（12–15） =======================

def test_12_mastery(client, learner):
    data = _data(client.get(f"{API}/mastery", headers=learner))
    _exact(data, {"status"})
    for kp_id, status in data["status"].items():
        assert kp_id in KP_IDS
        assert status in KP_STATUS


def test_13_mastery_check(client, learner, preserve_learner_state):
    # dev 库可能残留 dl=passed（7.2：已 passed 保持不变）→ 先清行再走 learning
    db = SessionLocal()
    db.query(Mastery).filter(
        Mastery.user_id == preserve_learner_state, Mastery.kp_id == "dl"
    ).delete()
    db.commit()
    db.close()
    client.post(f"{API}/resource/lecture", headers=learner,
                json={"kpId": "dl", "difficulty": "入门"})  # 先置 learning
    data = _data(client.post(f"{API}/mastery/dl/check", headers=learner))
    _exact(data, {"id", "status"})
    assert data == {"id": "dl", "status": "pending-check"}


def test_14_mastery_pass(client, learner):
    data = _data(client.post(f"{API}/mastery/dl/pass", headers=learner))
    _exact(data, {"id", "status"})
    assert data == {"id": "dl", "status": "passed"}
    # 幂等：重复 pass 结构不变
    again = _data(client.post(f"{API}/mastery/dl/pass", headers=learner))
    assert again == data


def test_15_journey(client, learner):
    data = _data(client.get(f"{API}/journey", headers=learner))
    _exact(data, {"hasDiagnosed", "hasGeneratedPath", "targetJobName",
                  "matchPct", "currentStep"})
    assert isinstance(data["hasDiagnosed"], bool)
    assert isinstance(data["hasGeneratedPath"], bool)
    assert data["targetJobName"] is None or isinstance(data["targetJobName"], str)
    assert data["matchPct"] is None or isinstance(data["matchPct"], int)
    assert data["currentStep"] in JOURNEY_STEPS


# ============================== Resource（16–22） ================================

def test_16_knowledge_point_meta(client, learner):
    data = _data(client.get(f"{API}/resource/knowledge-point/nn", headers=learner))
    _exact(data, {"id", "name", "description", "status"})
    assert data["id"] == "nn"
    assert data["status"] in KP_STATUS
    _data(client.get(f"{API}/resource/knowledge-point/nope", headers=learner),
          code=1004, status=404)


def test_17_lecture(client, learner):
    data = _data(client.post(f"{API}/resource/lecture", headers=learner,
                             json={"kpId": "nn", "difficulty": "初级"}))
    _exact(data, {"kpId", "difficulty", "markdown", "sources",
                  "hallucinationRate", "workflowId"}, optional={"generationMeta"})
    assert data["kpId"] == "nn" and data["difficulty"] == "初级"
    assert isinstance(data["markdown"], str) and data["markdown"].startswith("# ")
    assert isinstance(data["sources"], list) and data["sources"]
    for source in data["sources"]:
        _exact(source, {"title", "type", "confidence"})
        assert source["type"] in SOURCE_TYPES
        assert 0 <= source["confidence"] <= 1
    assert 0 <= data["hallucinationRate"] <= 1
    # B10：workflowId 标识产出该份讲义的工作流 trace（直出 mock 为 null，
    # 工作流回写后为字符串）；供前端「查看生成过程」回放
    assert data["workflowId"] is None or isinstance(data["workflowId"], str)
    # 难度档非法 → 1001（资源页五档：入门|初级|中级|高级|精通；"地狱" 不在档内）
    _data(client.post(f"{API}/resource/lecture", headers=learner,
                      json={"kpId": "nn", "difficulty": "地狱"}),
          code=1001, status=400)


def test_18_video(client, learner):
    data = _data(client.post(f"{API}/resource/video", headers=learner,
                             json={"kpId": "nn", "difficulty": "初级"}))
    _exact(data, {"videoUrl", "title", "scenes", "narration", "fps", "width",
                  "height", "durationInFrames"}, optional={"generationMeta"})
    assert data["videoUrl"] is None or isinstance(data["videoUrl"], str)
    assert isinstance(data["title"], str) and data["title"]
    # 分镜脚本：3-5 个场景，每场景 title/points/narration 随知识点动态生成
    assert isinstance(data["scenes"], list) and 3 <= len(data["scenes"]) <= 5
    for scene in data["scenes"]:
        _exact(scene, {"frame", "title", "points", "narration"})
        assert isinstance(scene["frame"], int)
        assert isinstance(scene["title"], str) and scene["title"]
        assert isinstance(scene["points"], list) and scene["points"]
        assert all(isinstance(p, str) and p for p in scene["points"])
        assert isinstance(scene["narration"], str) and scene["narration"]
    # narration[] 帧-旁白映射与 scenes 对齐（向后兼容字段）
    assert isinstance(data["narration"], list) and data["narration"]
    assert len(data["narration"]) == len(data["scenes"])
    for item in data["narration"]:
        _exact(item, {"frame", "text"})
        assert isinstance(item["frame"], int)


def test_19_mindmap(client, learner):
    data = _data(client.get(f"{API}/resource/mindmap/nn", headers=learner))
    _exact(data, {"markdown"}, optional={"generationMeta"})
    assert data["markdown"].startswith("# ")  # Markmap 大纲
    _data(client.get(f"{API}/resource/mindmap/nope", headers=learner),
          code=1004, status=404)


def test_20_diagram(client, learner):
    data = _data(client.get(f"{API}/resource/diagram/nn", headers=learner))
    _exact(data, {"mermaid"}, optional={"generationMeta"})
    assert data["mermaid"].startswith("flowchart")
    _data(client.get(f"{API}/resource/diagram/nope", headers=learner),
          code=1004, status=404)


def test_21_external_resources(client, learner):
    data = _data(client.get(f"{API}/resource/external/nn", headers=learner))
    assert isinstance(data, list) and data
    for item in data:
        _exact(item, {"id", "type", "title", "source", "url",
                      "relevance", "credibility", "reason"},
               optional={"embed", "duration"})
        assert item["type"] in EXTERNAL_TYPES
        assert 0 <= item["relevance"] <= 100
        assert 0 <= item["credibility"] <= 100
    # relevance 降序（8.6）
    relevances = [i["relevance"] for i in data]
    assert relevances == sorted(relevances, reverse=True)


def test_22_tutor_chat(client, learner):
    # JSON 模式（8.7）
    data = _data(client.post(f"{API}/resource/tutor/chat", headers=learner,
                             json={"kpId": "nn", "sessionId": None,
                                   "message": "为什么需要激活函数？"}))
    _exact(data, {"sessionId", "reply", "suggestions"}, optional={"generationMeta"})
    assert isinstance(data["reply"], str) and data["reply"]
    assert isinstance(data["suggestions"], list)
    # SSE 模式（15.4）：delta 逐条 + event: done 收尾
    res = client.post(f"{API}/resource/tutor/chat",
                      headers={**learner, "Accept": "text/event-stream"},
                      json={"kpId": "nn", "sessionId": data["sessionId"],
                            "message": "等价于线性变换"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    text = res.text
    assert 'data: {"delta"' in text.replace("data:{", 'data: {')
    assert "event: done" in text
    done_payload = text.split("event: done", 1)[1]
    assert "sessionId" in done_payload and "suggestions" in done_payload


# ============================== Quiz & Reinforce（23–25） ========================

def test_23_quiz(client, learner):
    data = _data(client.get(f"{API}/quiz/nn", headers=learner))
    _exact(data, {"questions"})
    assert isinstance(data["questions"], list) and data["questions"]
    for q in data["questions"]:
        _assert_quiz_question(q)
    shared["quiz_questions"] = data["questions"]


def test_24_quiz_submit(client, learner):
    # 客观题以正确答案作答；简答题（C-fix 批2）以「参考要点」拼接文本作答，
    # 触发 AI 评分（mock 确定性给高覆盖分），综合 ≥60 → passed。
    questions = shared["quiz_questions"]
    short_ids = {q["question_id"] for q in questions if q["question_type"] == "short_answer"}
    objective = [q for q in questions if q["question_type"] != "short_answer"]
    answers = []
    for q in questions:
        if q["question_type"] == "short_answer":
            answers.append({"question_id": q["question_id"], "answer": "；".join(q["correct_answer"])})
        else:
            answers.append({"question_id": q["question_id"], "answer": q["correct_answer"]})
    data = _data(client.post(f"{API}/quiz/nn/submit", headers=learner,
                             json={"answers": answers}))
    _exact(data, {"score", "passed", "correctCount", "total",
                  "wrong", "shortAnswers", "masteryUpdated"}, optional={"generationMeta"})
    assert data["total"] == len(answers)
    assert data["correctCount"] == len(objective)  # 简答不计入客观答对数
    assert data["wrong"] == []
    assert data["score"] >= 60 and data["passed"] is True  # 客观全对 + 简答高覆盖
    assert isinstance(data["shortAnswers"], list) and len(data["shortAnswers"]) == len(short_ids)
    for sa in data["shortAnswers"]:
        _exact(sa, {"questionId", "score", "comment"})
        assert sa["questionId"] in short_ids
        assert isinstance(sa["score"], int) and 0 <= sa["score"] <= 100
        assert isinstance(sa["comment"], str) and sa["comment"]
    _exact(data["masteryUpdated"], {"id", "status"})
    assert data["masteryUpdated"] == {"id": "nn", "status": "passed"}


def test_25_reinforce(client, learner):
    data = _data(client.post(f"{API}/reinforce", headers=learner,
                             json={"kpId": "nn", "wrongQuestionIds": ["nn_q1", "nn_q3"]}))
    assert isinstance(data, list) and len(data) == 2
    for card in data:
        _exact(card, {"questionId", "point", "recap", "practice"},
               optional={"generationMeta"})
        _assert_quiz_question(card["practice"])
        assert card["practice"]["question_id"] == f"{card['questionId']}-r"


# ============================== Knowledge Graph（26） ============================

def test_26_knowledge_graph(client, learner):
    data = _data(client.get(f"{API}/knowledge-graph", headers=learner))
    _exact(data, {"nodes", "links", "categories"})
    assert len(data["nodes"]) == 12 and len(data["links"]) == 14
    assert {n["id"] for n in data["nodes"]} == GRAPH_NODE_IDS
    for node in data["nodes"]:
        _exact(node, {"id", "name", "category", "value"})
        assert node["category"] in (0, 1, 2, 3)
        assert 0 <= node["value"] <= 100
    node_ids = {n["id"] for n in data["nodes"]}
    for link in data["links"]:
        _exact(link, {"source", "target"})
        assert link["source"] in node_ids and link["target"] in node_ids
    assert [c["name"] for c in data["categories"]] == ["已掌握", "学习中", "待学习", "知识盲区"]


# ============================== Workflow（27–28） ================================

def test_27_workflow_execute(client, learner):
    data = _data(client.post(f"{API}/workflow/execute", headers=learner,
                             json={"targetJobId": "llm-app", "kpId": "nn"}))
    _exact(data, {"workflowId"})
    shared["workflow_id"] = data["workflowId"]


def test_28_workflow_status_and_ws(client, learner):
    wf_id = shared["workflow_id"]
    # 轮询（11.2）：每帧结构合规，直至 complete
    deadline = time.time() + 30
    data = None
    while time.time() < deadline:
        data = _data(client.get(f"{API}/workflow/{wf_id}", headers=learner))
        _assert_workflow_snapshot(data)
        if data["phase"] == "complete":
            break
        time.sleep(0.1)
    assert data is not None and data["phase"] == "complete"
    # WS：连接即推全量快照帧，同样套统一信封
    with client.websocket_connect(f"{API}/ws/workflow/{wf_id}") as ws:
        frame = ws.receive_json()
        assert set(frame) == {"code", "message", "data", "traceId"}
        assert frame["code"] == 0
        _assert_workflow_snapshot(frame["data"])
    # 未知 workflowId → 1004
    _data(client.get(f"{API}/workflow/wf_nope", headers=learner),
          code=1004, status=404)


# ============================== Dashboard（29） ==================================

def test_29_dashboard_overview(client, learner):
    data = _data(client.get(f"{API}/dashboard/overview", headers=learner))
    _exact(data, {"overall_level", "overall_score", "knowledge_graph_coverage",
                  "learned_resources", "strong_topics", "weak_topics",
                  "radar", "preferences", "comparison", "targetSummary"})
    assert isinstance(data["overall_score"], (int, float))
    assert 0 <= data["knowledge_graph_coverage"] <= 1
    for topic in data["strong_topics"] + data["weak_topics"]:
        _exact(topic, {"name", "mastery"})
    _exact(data["radar"], {"dimensions", "values"})
    # C2：雷达只含能力维（知识点掌握度），由微测/quiz 写入 Mastery 的能力分驱动——
    # 轴值类型自洽；偏好维改类型标签、不上 0-100 轴。
    assert isinstance(data["radar"]["dimensions"], list)
    assert isinstance(data["radar"]["values"], list)
    assert len(data["radar"]["dimensions"]) == len(data["radar"]["values"])
    assert all(isinstance(name, str) for name in data["radar"]["dimensions"])
    assert all(isinstance(v, (int, float)) for v in data["radar"]["values"])
    # preferences：偏好类型标签（无分数、不上轴）
    assert isinstance(data["preferences"], list)
    for pref in data["preferences"]:
        _exact(pref, {"key", "label", "value", "optionKey"})
        assert "score" not in pref
    _exact(data["comparison"], {"betterThanPct"})
    _exact(data["targetSummary"], {"hasDiagnosed", "targetJobName", "matchPct"})


# ============================== Task（30） =======================================

def test_30_task_polling(client, learner):
    task = _wait_task(client, learner, shared["path_task_id"])
    _exact(task, {"taskId", "status", "result", "error"}, optional={"progress"})
    assert task["status"] == "succeeded"
    assert task["error"] is None
    _exact(task["result"], {"lessons"})  # 15.2：路径任务产物 {lessons}
    assert len(task["result"]["lessons"]) >= 6  # 会话三：按目标聚焦细化，长度可 > 6
    _data(client.get(f"{API}/tasks/t_nope", headers=learner), code=1004, status=404)


# ============================== Admin（31–36） ===================================

def test_31_admin_kb_upload(client, admin):
    content = (
        "# B8 契约快照测试文档\n\n"
        "反向传播通过链式法则计算损失对各层参数的梯度，供梯度下降更新权重。\n"
    ).encode("utf-8")
    res = client.post(
        f"{API}/admin/kb/upload", headers=admin,
        files=[("files", ("b8-contract-test.md", content, "text/markdown"))],
        data={"category": "文档", "tags": "b8,契约测试"},
    )
    data = _data(res)
    _exact(data, {"taskId", "documents"})
    assert len(data["documents"]) == 1
    doc = data["documents"][0]
    _exact(doc, {"id", "title", "filename", "size", "category",
                 "status", "chunks", "uploadedAt"})
    assert doc["status"] in DOC_STATUS
    assert doc["filename"] == "b8-contract-test.md"
    shared["kb_doc_id"] = doc["id"]
    # 入库任务跑完（向量化 + 写 Chroma）
    task = _wait_task(client, admin, data["taskId"])
    assert task["status"] == "succeeded", task


def test_32_admin_kb_documents(client, admin):
    data = _data(client.get(f"{API}/admin/kb/documents", headers=admin,
                            params={"page": 1, "pageSize": 50}))
    _exact(data, {"items", "total", "page", "pageSize"})
    assert data["page"] == 1 and data["pageSize"] == 50
    assert any(item["id"] == shared["kb_doc_id"] for item in data["items"])
    for item in data["items"]:
        _exact(item, {"id", "title", "filename", "size", "category",
                      "status", "chunks", "uploadedAt"})
        assert item["status"] in DOC_STATUS


def test_33_admin_kb_delete(client, admin):
    doc_id = shared["kb_doc_id"]
    data = _data(client.delete(f"{API}/admin/kb/documents/{doc_id}", headers=admin))
    _exact(data, {"id", "deleted", "removedChunks"})
    assert data == {"id": doc_id, "deleted": True, "removedChunks": data["removedChunks"]}
    assert isinstance(data["removedChunks"], int)
    _data(client.delete(f"{API}/admin/kb/documents/{doc_id}", headers=admin),
          code=1004, status=404)


def test_34_admin_kb_search_test(client, admin):
    data = _data(client.post(f"{API}/admin/kb/search-test", headers=admin,
                             json={"query": "反向传播的梯度如何计算", "topK": 5}))
    _exact(data, {"rerankerUsed", "results"})
    assert isinstance(data["rerankerUsed"], bool)
    assert isinstance(data["results"], list) and data["results"]
    for item in data["results"]:
        _exact(item, {"chunkId", "documentId", "documentTitle", "content",
                      "score", "vectorScore", "sourceLocation"},
               optional={"bm25Score"})
        assert 0 <= item["score"] <= 1


def test_35_admin_prompts(client, admin):
    data = _data(client.get(f"{API}/admin/prompts/generation", headers=admin))
    _exact(data, {"agentId", "name", "template", "variables", "version", "updatedAt"})
    assert data["agentId"] == "generation"
    assert isinstance(data["variables"], list)
    # PUT 原模板回写（version 自增，内容不变 → 热更新链路结构断言，不污染模板）
    updated = _data(client.put(f"{API}/admin/prompts/generation", headers=admin,
                               json={"template": data["template"]}))
    _exact(updated, {"agentId", "version", "updatedAt", "hotReloaded"})
    assert updated["version"] == data["version"] + 1
    assert updated["hotReloaded"] is True
    # 缺失必需占位符 → 1001
    _data(client.put(f"{API}/admin/prompts/generation", headers=admin,
                     json={"template": "没有任何占位符的模板"}),
          code=1001, status=400)
    # 未知 agentId → 1004
    _data(client.get(f"{API}/admin/prompts/unknown", headers=admin),
          code=1004, status=404)


def test_36_admin_metrics(client, admin):
    data = _data(client.get(f"{API}/admin/metrics", headers=admin))
    _exact(data, {"hallucinationRate", "adaptationRate", "coverageRate",
                  "kbDocuments", "kbChunks", "generatedResources", "updatedAt"})
    assert 0 <= data["hallucinationRate"] <= 1
    assert 0 <= data["adaptationRate"] <= 1
    assert 0 <= data["coverageRate"] <= 1
    for key in ("kbDocuments", "kbChunks", "generatedResources"):
        assert isinstance(data[key], int)
    # B8 量化目标（14.6）：幻觉率 <5%、适配率 ≥85%、覆盖率 ≥90%
    assert data["hallucinationRate"] < 0.05
    assert data["adaptationRate"] >= 0.85
    assert data["coverageRate"] >= 0.90


# ============================== Admin Users / Register（16.1–16.4，B9） =========

@pytest.fixture(scope="module", autouse=True)
def cleanup_b9_users(client):
    """B9：模块结束清理所有 b9snap_* 一次性账号及其旅程/掌握度（不污染 dev 库）。"""
    yield
    db = SessionLocal()
    stale = db.query(User).filter(User.username.like("b9snap_%")).all()
    for u in stale:
        db.query(Mastery).filter(Mastery.user_id == u.id).delete()
        db.query(Journey).filter(Journey.user_id == u.id).delete()
        db.delete(u)
    db.commit()
    db.close()


def test_37_register(client):
    """16.1 注册：结构与 login 同构（token+user）；查重/弱密码 → 1001。"""
    body = {"username": "b9snap_user", "password": "snap123456"}
    data = _data(client.post(f"{API}/auth/register", json=body))
    _exact(data, {"token", "expiresIn", "user"})
    assert isinstance(data["token"], str) and data["token"]
    assert isinstance(data["expiresIn"], int)
    user = data["user"]
    _exact(user, {"userId", "username", "displayName", "role",
                  "hasDiagnosed", "hasGeneratedPath"})
    assert user["role"] == "learner"  # 注册账号固定 learner（16.1）
    assert user["hasDiagnosed"] is False and user["hasGeneratedPath"] is False
    shared["b9_user_id"] = user["userId"]
    shared["b9_headers"] = {"Authorization": f"Bearer {data['token']}"}
    shared["b9_login"] = body
    # 重复用户名 → 1001
    _data(client.post(f"{API}/auth/register", json=body), code=1001, status=400)
    # 弱密码（<6）→ 1001
    _data(client.post(f"{API}/auth/register",
                      json={"username": "b9snap_weak", "password": "123"}),
          code=1001, status=400)


def test_38_admin_users_list(client, admin):
    """16.2 用户列表：分页结构 + 行精确字段集；定位注册账号。"""
    data = _data(client.get(f"{API}/admin/users", headers=admin,
                            params={"page": 1, "pageSize": 100}))
    _exact(data, {"items", "total", "page", "pageSize"})
    assert data["page"] == 1 and data["pageSize"] == 100
    assert isinstance(data["total"], int) and data["total"] >= 2  # 至少种子 admin+learner
    for item in data["items"]:
        _exact(item, {"userId", "username", "role", "isActive", "hasDiagnosed",
                      "passedKpCount", "createdAt", "lastActiveAt"})
        assert item["role"] in {"learner", "admin"}
        assert isinstance(item["isActive"], bool)
        assert isinstance(item["hasDiagnosed"], bool)
        assert isinstance(item["passedKpCount"], int) and item["passedKpCount"] >= 0
        assert item["createdAt"] is None or isinstance(item["createdAt"], str)
        assert item["lastActiveAt"] is None or isinstance(item["lastActiveAt"], str)
    ours = [i for i in data["items"] if i["userId"] == shared["b9_user_id"]]
    assert ours and ours[0]["isActive"] is True


def test_39_admin_reset_progress(client, admin):
    """16.3 重置进度：清空掌握度 + 复位旅程；禁对 admin → 1003；未知 → 1004。"""
    headers = shared["b9_headers"]
    # 先让账号产生一条 passed 掌握度
    client.post(f"{API}/resource/lecture", headers=headers,
                json={"kpId": "nn", "difficulty": "入门"})
    _data(client.post(f"{API}/mastery/nn/pass", headers=headers))
    data = _data(client.post(
        f"{API}/admin/users/{shared['b9_user_id']}/reset-progress", headers=admin))
    _exact(data, {"userId", "hasDiagnosed", "masteryCleared"})
    assert data["userId"] == shared["b9_user_id"]
    assert data["hasDiagnosed"] is False
    assert isinstance(data["masteryCleared"], int) and data["masteryCleared"] >= 1
    # 复位生效：掌握度清空
    assert _data(client.get(f"{API}/mastery", headers=headers))["status"] == {}
    # 幂等：再次重置 masteryCleared = 0
    again = _data(client.post(
        f"{API}/admin/users/{shared['b9_user_id']}/reset-progress", headers=admin))
    assert again["masteryCleared"] == 0
    # 禁对 admin 角色 → 1003；未知用户 → 1004
    _data(client.post(f"{API}/admin/users/u_10000/reset-progress", headers=admin),
          code=1003, status=403)
    _data(client.post(f"{API}/admin/users/u_nope/reset-progress", headers=admin),
          code=1004, status=404)


def test_40_admin_toggle_active(client, admin):
    """16.4 启停：翻转 isActive；禁用账号登录 → 1002；禁对 admin → 1003。"""
    uid = shared["b9_user_id"]
    data = _data(client.post(f"{API}/admin/users/{uid}/toggle-active", headers=admin))
    _exact(data, {"userId", "isActive"})
    assert data["userId"] == uid and data["isActive"] is False
    # 禁用后登录被拦 → 1002
    _data(client.post(f"{API}/auth/login", json=shared["b9_login"]),
          code=1002, status=401)
    # 再翻转回启用 → 登录恢复
    back = _data(client.post(f"{API}/admin/users/{uid}/toggle-active", headers=admin))
    assert back["isActive"] is True
    _data(client.post(f"{API}/auth/login", json=shared["b9_login"]))
    # 禁对 admin 角色 → 1003；未知用户 → 1004
    _data(client.post(f"{API}/admin/users/u_10000/toggle-active", headers=admin),
          code=1003, status=403)
    _data(client.post(f"{API}/admin/users/u_nope/toggle-active", headers=admin),
          code=1004, status=404)


# ============================== 错误信封抽查 =====================================

def test_error_envelopes(client, learner):
    """1002/1003 错误码与信封结构（1.3）。"""
    _data(client.get(f"{API}/journey"), code=1002, status=401)  # 未带 token
    _data(client.get(f"{API}/admin/metrics", headers=learner),
          code=1003, status=403)  # learner 访问管理端
    # B9：learner 访问管理端用户管理三接口 → 1003
    _data(client.get(f"{API}/admin/users", headers=learner), code=1003, status=403)
    _data(client.post(f"{API}/admin/users/u_10001/reset-progress", headers=learner),
          code=1003, status=403)
    _data(client.post(f"{API}/admin/users/u_10001/toggle-active", headers=learner),
          code=1003, status=403)
