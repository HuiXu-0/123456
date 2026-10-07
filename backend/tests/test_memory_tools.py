"""R4.2 契约测试：Memory Tools + Learning Tools（注册 / Schema / 鉴权 / 复用 / Mock）。

验收口径（R4.2 实施报告 §1–§8），逐条对应需求的 10 个覆盖点：

1. Tool Registry 注册成功 —— 4 个 Spec 可发现，且与 `BaseAgent.tools` 声明一致
2. Tool schema 正确 —— JSON Schema / 必填 / 范围 / `extra=forbid`
3. 参数非法时拒绝 —— 缺参、越界、未知字段、空 user_id
4. 正常调用返回 **JSON 可序列化**结果
5. **用户隔离** —— 经 Registry 的越权守卫 + 跨用户数据不可见
6. mock 模式可用且**确定性**（同参同结果）
7. `get_learning_profile` **复用**既有画像服务（monkeypatch 委托证明）
8. `get_mastery_state` **复用**既有掌握度服务（未重写算法）
9. `get_learning_path` **复用** `planner_agent.plan_path`
10. Tool **不直接暴露 DB Session**

另含：白名单执行（未注册名拒绝）、最小权限声明。

零网络、零计费（conftest 强制 mock provider + 临时隔离库）。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.agents import registry as agent_registry
from app.agents.tools import (
    LEARNING_MASTERY_OPERATION,
    LEARNING_MASTERY_TOOL,
    LEARNING_PATH_OPERATION,
    LEARNING_PATH_TOOL,
    LEARNING_PROFILE_OPERATION,
    LEARNING_PROFILE_TOOL,
    MEMORY_SEARCH_OPERATION,
    MEMORY_SEARCH_TOOL,
    AgentTool,
    ToolPermissionError,
    ToolSpec,
    get_tool_registry,
)
from app.agents.tools.learning_tool import (
    GetLearningPathTool,
    GetLearningProfileTool,
    mock_get_learning_path,
    mock_get_learning_profile,
    mock_get_mastery_state,
)
from app.agents.tools.memory_tool import mock_search_user_memory
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import Mastery
from app.services import mastery as mastery_service
from app.services import student_portrait as portrait_service

_ACTOR_A = "u_r42_alice"
_ACTOR_B = "u_r42_bob"

R42_TOOLS = (
    MEMORY_SEARCH_TOOL,
    LEARNING_PROFILE_TOOL,
    LEARNING_MASTERY_TOOL,
    LEARNING_PATH_TOOL,
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # 触发 lifespan：建表 + 幂等种子
        yield c


@pytest.fixture(scope="module")
def registry(client):
    """导入即注册；断言 4 个 Spec 都在（R4.2 §5.1 可发现）。"""
    reg = get_tool_registry()
    for name in R42_TOOLS:
        assert name in reg.available_tools, f"{name} 未注册"
    return reg


@pytest.fixture(scope="module", autouse=True)
def _seed_isolation_evidence(client):
    """给 A/B 各埋一条不同的 mastery，用于跨用户隔离验证（幂等）。"""
    session = SessionLocal()
    try:
        if session.query(Mastery).filter_by(user_id=_ACTOR_A, kp_id="nn").first() is None:
            session.add(
                Mastery(user_id=_ACTOR_A, kp_id="nn", status="passed",
                        score=88, confidence=0.9, score_source="quiz")
            )
        if session.query(Mastery).filter_by(user_id=_ACTOR_B, kp_id="dl").first() is None:
            session.add(
                Mastery(user_id=_ACTOR_B, kp_id="dl", status="learning",
                        score=45, confidence=0.5, score_source="diagnostic")
            )
        session.commit()
    finally:
        session.close()


# ── 1. Registry 注册成功 ─────────────────────────────────────────────────────


def test_four_tools_registered_and_discoverable(registry):
    available = set(registry.available_tools)
    assert set(R42_TOOLS) <= available
    # 工厂形态与 Spec 形态共存于同一发现入口
    assert {"trusted_rag"} <= available
    # 4 个 Spec 各有独立 name（无重名）
    names = [spec.name for spec in registry.specs()]
    assert len(names) == len(set(names))


def test_spec_list_is_queryable(registry):
    names = {spec.name for spec in registry.specs()}
    assert set(R42_TOOLS) <= names
    for name in R42_TOOLS:
        spec = registry.spec(name)
        assert isinstance(spec, ToolSpec)
        assert spec.description and spec.operation


def test_every_registered_spec_has_valid_schema(registry):
    for spec in registry.specs():
        assert spec.description.strip()
        assert spec.name.strip()
        assert isinstance(spec.args_schema, type)
        assert issubclass(spec.args_schema, BaseModel)
        assert spec.json_schema()["type"] == "object"


def test_declared_agent_tools_all_registered():
    """BaseAgent.tools 声明的每个工具都必须真的在 Registry 里（声明 ≠ 悬空）。"""
    registered = set(get_tool_registry().available_tools)
    for agent_id in ("generation", "critic"):
        declared = agent_registry.tools_of(agent_id)
        assert declared, agent_id
        missing = set(declared) - registered
        assert not missing, f"{agent_id} 声明了未注册工具: {missing}"


def test_minimal_privilege_on_agent_tools():
    """最小权限：未授权 Agent 无工具；critic 比 generation 少 learning/path。"""
    assert agent_registry.tools_of("planner") == ()
    assert agent_registry.tools_of("dialogue") == ()
    assert agent_registry.tools_of("evaluation") == ()
    assert agent_registry.tools_of("diagnosis") == ()
    assert agent_registry.tools_of("feynman") == ()

    critic = set(agent_registry.tools_of("critic"))
    generation = set(agent_registry.tools_of("generation"))
    assert critic < generation  # 真子集
    assert LEARNING_PATH_TOOL in generation and LEARNING_PATH_TOOL not in critic


def test_whitelist_rejects_unknown_tool(registry):
    with pytest.raises(KeyError, match="unknown tool spec"):
        registry.execute("memory/delete_everything", actor_id=_ACTOR_A, user_id=_ACTOR_A)
    with pytest.raises(KeyError):
        registry.spec("not-registered")


def test_legacy_rag_tool_still_available(registry):
    """既有工厂形态不受影响（R4.3-E3 的 trusted_rag 仍在册）。"""
    from app.agents.tools.registry import RAG_TOOL_NAME

    assert RAG_TOOL_NAME in registry.available_tools


# ── 2. Schema 正确 ───────────────────────────────────────────────────────────


def test_args_schema_shape(registry):
    profile = registry.spec(LEARNING_PROFILE_TOOL)
    schema = profile.json_schema()
    assert schema["type"] == "object"
    assert schema["properties"]["user_id"]["type"] == "string"
    assert "user_id" in schema["required"]
    assert profile.to_dict()["requiresUserId"] is True
    assert profile.to_dict()["operation"] == LEARNING_PROFILE_OPERATION

    memory = registry.spec(MEMORY_SEARCH_TOOL)
    props = memory.json_schema()["properties"]
    assert props["limit"]["minimum"] == 1
    assert props["limit"]["maximum"] == 100
    assert props["query"]["type"] == "string"
    assert memory.json_schema().get("additionalProperties") is False  # extra=forbid

    path = registry.spec(LEARNING_PATH_TOOL)
    assert path.json_schema()["properties"]["narrate"]["default"] is False


def test_every_registered_spec_has_nonblank_description(registry):
    for spec in registry.specs():
        assert spec.description.strip()
        assert spec.name.strip()
        assert issubclass(spec.args_schema, object)


# ── 3. 参数非法时拒绝 ────────────────────────────────────────────────────────


def test_missing_required_user_id_rejected(registry):
    with pytest.raises(ValidationError):
        registry.execute(LEARNING_PROFILE_TOOL, actor_id=_ACTOR_A)


def test_blank_user_id_rejected(registry):
    with pytest.raises(ValidationError):
        registry.execute(LEARNING_PROFILE_TOOL, actor_id=_ACTOR_A, user_id="")


def test_limit_out_of_range_rejected(registry):
    for bad in (0, -1, 101, 99999):
        with pytest.raises(ValidationError):
            registry.execute(
                MEMORY_SEARCH_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A, limit=bad
            )


def test_unknown_field_rejected():
    """extra=forbid：多余的字段一律拒绝（防止注入）。"""
    registry = get_tool_registry()
    with pytest.raises(ValidationError):
        registry.execute(
            MEMORY_SEARCH_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A, drop_tables=True
        )


def test_overlong_query_rejected(registry):
    with pytest.raises(ValidationError):
        registry.execute(
            MEMORY_SEARCH_TOOL,
            actor_id=_ACTOR_A, user_id=_ACTOR_A, query="x" * 500,
        )


# ── 4. 正常调用返回 JSON 可序列化结果 ────────────────────────────────────────


def test_results_are_json_serializable(registry):
    for name, operation, kwargs in (
        (MEMORY_SEARCH_TOOL, MEMORY_SEARCH_OPERATION, {"query": "nn"}),
        (LEARNING_PROFILE_TOOL, LEARNING_PROFILE_OPERATION, {}),
        (LEARNING_MASTERY_TOOL, LEARNING_MASTERY_OPERATION, {}),
        (LEARNING_PATH_TOOL, LEARNING_PATH_OPERATION, {}),
    ):
        result = registry.execute(name, actor_id=_ACTOR_A, user_id=_ACTOR_A, **kwargs)
        # 可序列化（可进 State / trace / 返回给 LLM）
        text = json.dumps(result, ensure_ascii=False, default=str)
        assert text
        assert json.loads(text)["tool"] == name
        # 统一结构 + 注册名与操作名一致
        assert {"tool", "operation", "mock", "userId"} <= set(result)
        assert result["operation"] == operation
        assert result["userId"] == _ACTOR_A
        assert result["mock"] is False


# ── 5. 用户隔离 ──────────────────────────────────────────────────────────────


def test_cross_user_access_is_rejected(registry):
    """经 Registry 的越权守卫：绑定 A、请求 B → 拒绝执行。"""
    with pytest.raises(ToolPermissionError):
        registry.execute(MEMORY_SEARCH_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_B)
    with pytest.raises(ToolPermissionError):
        registry.execute(LEARNING_PROFILE_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_B)


def test_tool_guard_rejects_direct_spoof():
    """直构路径同样受守卫（为 R4.3 Tool Loop 预留的纵深防御）。"""
    tool = GetLearningProfileTool(user_id=_ACTOR_A)
    assert tool.user_id == _ACTOR_A
    with pytest.raises(ToolPermissionError, match="越权"):
        tool.execute(user_id=_ACTOR_B)


def test_data_is_isolated_between_users(registry):
    """A 的掌握度数据绝不出现在 B 的检索结果里。"""
    alice = registry.execute(MEMORY_SEARCH_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A, query="")
    bob = registry.execute(MEMORY_SEARCH_TOOL, actor_id=_ACTOR_B, user_id=_ACTOR_B, query="")

    assert all(item["user_id"] == _ACTOR_A for item in alice["evidence"])
    assert all(item["user_id"] == _ACTOR_B for item in bob["evidence"])

    alice_kps = {i["subject"] for i in alice["evidence"] if i["kind"] == "mastery"}
    bob_kps = {i["subject"] for i in bob["evidence"] if i["kind"] == "mastery"}
    assert "nn" in alice_kps and "nn" not in bob_kps
    assert "dl" in bob_kps and "dl" not in alice_kps


def test_empty_user_id_is_rejected(registry):
    """空 / 空白 user_id 必须被拒绝 —— 由两层校验各司其职（R4.5.2 修正断言）。"""
    # ① 完全为空 → args_schema 的 min_length 在 schema 层拒绝
    with pytest.raises(ValidationError):
        registry.execute(MEMORY_SEARCH_TOOL, actor_id=_ACTOR_A, user_id="")
    # ② 纯空白能通过 min_length，由越权校验拒绝（strip 后 ≠ 绑定身份）
    #    —— 仍是"被拒绝"，只是拒绝方不同；不得放行
    with pytest.raises(ToolPermissionError):
        registry.execute(MEMORY_SEARCH_TOOL, actor_id=_ACTOR_A, user_id="   ")


# ── 6. Mock 模式可用且确定性 ─────────────────────────────────────────────────


def test_mock_helpers_are_deterministic():
    """同参数 → 同结果（无随机、无网络）。"""
    assert mock_search_user_memory(user_id="u", query="q", limit=5) == (
        mock_search_user_memory(user_id="u", query="q", limit=5)
    )
    assert mock_get_learning_profile(user_id="u") == mock_get_learning_profile(user_id="u")
    assert mock_get_mastery_state(user_id="u", kp_id="nn") == mock_get_mastery_state(
        user_id="u", kp_id="nn"
    )
    first = mock_get_learning_path(user_id="u")
    assert first == mock_get_learning_path(user_id="u")
    assert first["mock"] is True and first["lessons"]


def test_mock_respects_limit():
    assert len(mock_search_user_memory(user_id="u", limit=1)["evidence"]) == 1
    assert len(mock_search_user_memory(user_id="u", limit=2)["evidence"]) == 2


def test_memory_tool_degrades_to_mock_when_real_fails(monkeypatch):
    """真实路径因基础设施异常失败 → 回落确定性 mock，且**显式标记 degraded**。"""
    import app.agents.tools.memory_tool as memory_tool_mod

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(memory_tool_mod, "get_memory", _boom)
    result = get_tool_registry().execute(
        MEMORY_SEARCH_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A, query="x"
    )
    assert result["mock"] is True
    assert result["degraded"] is True
    assert result["errorType"] == "RuntimeError"
    assert result["count"] >= 0


def test_learning_tool_degrades_to_mock_when_real_fails(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("no db")

    monkeypatch.setattr(portrait_service, "get_portrait", _boom)
    result = get_tool_registry().execute(
        LEARNING_PROFILE_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A
    )
    assert result["mock"] is True and result["degraded"] is True
    assert result["portrait"]["dimensions"]


# ── 7–9. 复用既有业务能力（委托证明，不重写）────────────────────────────────


def test_learning_profile_reuses_portrait_service(monkeypatch):
    """monkeypatch 掉画像服务 → Tool 结果必须来自它，证明未重写画像计算。"""
    sentinel = {"dimensions": [{"key": "marker", "label": "REUSED", "value": "from-service"}]}
    called: list[str] = []

    def _fake(db, user_id):
        called.append(user_id)
        return sentinel

    monkeypatch.setattr(portrait_service, "get_portrait", _fake)
    result = get_tool_registry().execute(
        LEARNING_PROFILE_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A
    )
    assert called == [_ACTOR_A]
    # Tool 直接透传服务返回，未做任何画像计算
    assert result["portrait"] == sentinel


def test_mastery_state_reuses_mastery_service(monkeypatch):
    """证明掌握度口径来自既有 service，Tool 未自行实现算法。"""
    monkeypatch.setattr(mastery_service, "get_status_map", lambda db, uid: {"nn": "passed"})
    monkeypatch.setattr(
        mastery_service, "get_score_map", lambda db, uid: {"nn": {"score": 88, "source": "quiz"}}
    )
    result = get_tool_registry().execute(
        LEARNING_MASTERY_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A
    )
    assert result["status"] == {"nn": "passed"}
    assert result["scores"]["nn"]["score"] == 88
    assert result["mock"] is False


def test_mastery_state_filters_by_kp(monkeypatch):
    monkeypatch.setattr(
        mastery_service, "get_status_map", lambda db, uid: {"nn": "passed", "dl": "learning"}
    )
    monkeypatch.setattr(
        mastery_service, "get_score_map",
        lambda db, uid: {"nn": {"score": 88, "source": "quiz"}, "dl": {"score": 45, "source": "diagnostic"}},
    )
    result = get_tool_registry().execute(
        LEARNING_MASTERY_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A, kp_id="dl"
    )
    assert result["status"] == {"dl": "learning"}
    assert set(result["scores"]) == {"dl"}


def test_learning_path_reuses_planner(monkeypatch):
    """直接复用 planner_agent.plan_path —— 排序/先修/裁剪算法不复制。"""
    from app.agents import planner_agent

    captured: dict = {}

    def _fake_plan_path(db, *, user_id, target_job_id=None, narrate=True):
        captured.update(
            user_id=user_id, target_job_id=target_job_id, narrate=narrate
        )
        return {"lessons": [{"sequence": 1, "topic": "神经网络"}], "summary": "planner 产出"}

    monkeypatch.setattr(planner_agent, "plan_path", _fake_plan_path)
    result = get_tool_registry().execute(
        LEARNING_PATH_TOOL,
        actor_id=_ACTOR_A, user_id=_ACTOR_A, target_job_id="job_1",
    )
    assert captured == {"user_id": _ACTOR_A, "target_job_id": "job_1", "narrate": False}
    assert result["lessons"] == [{"sequence": 1, "topic": "神经网络"}]
    assert result["summary"] == "planner 产出"


def test_learning_path_defaults_to_deterministic_narration(monkeypatch, registry):
    """narrate 默认 False → plan_path 走确定性模板（可重复，不受 provider 影响）。"""
    from app.agents import planner_agent

    seen: list[bool] = []
    original = planner_agent.plan_path

    def _spy(db, **kwargs):
        seen.append(bool(kwargs.get("narrate", True)))
        return original(db, **kwargs)

    monkeypatch.setattr(planner_agent, "plan_path", _spy)
    registry.execute(LEARNING_PATH_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A)
    assert seen == [False]


# ── 10. Tool 不直接暴露 DB Session ───────────────────────────────────────────


def test_tools_do_not_expose_db_session(registry):
    forbidden = ("db", "session", "Session", "engine", "cursor", "connection")
    tools = {
        LEARNING_PROFILE_TOOL: registry.spec(LEARNING_PROFILE_TOOL).factory(user_id=_ACTOR_A),
        LEARNING_MASTERY_TOOL: registry.spec(LEARNING_MASTERY_TOOL).factory(user_id=_ACTOR_A),
        # 直构也一样（工厂与直构共用同一个类）
        LEARNING_PATH_TOOL: GetLearningPathTool(user_id=_ACTOR_A),
    }
    for name, tool in tools.items():
        assert isinstance(tool, AgentTool)
        public = [attr for attr in dir(tool) if not attr.startswith("_")]
        for bad in forbidden:
            assert bad not in public, f"{name} 暴露了 {bad}"
        # 唯一与会话相关的公开面是**工厂**，且它返回 callable 而非 Session 实例
        assert callable(tool.session_factory)
        assert not any(
            isinstance(getattr(tool, attr, None), Session) for attr in public
        )


def test_tool_results_contain_no_orm_objects(registry):
    for name in R42_TOOLS:
        result = registry.execute(name, actor_id=_ACTOR_A, user_id=_ACTOR_A)
        assert not any(
            hasattr(value, "_sa_instance_mapper")
            for value in result.values()
            if not isinstance(value, (str, int, float, bool, type(None)))
        )


def test_memory_tool_never_returns_session_object(registry):
    result = registry.execute(
        MEMORY_SEARCH_TOOL, actor_id=_ACTOR_A, user_id=_ACTOR_A, query=""
    )
    # session 字段要么为 None，要么是纯 dict 结构
    session_field = result.get("session")
    assert session_field is None or isinstance(session_field, dict)
    if isinstance(session_field, dict):
        assert set(session_field) <= {"sessionId", "messages"}
