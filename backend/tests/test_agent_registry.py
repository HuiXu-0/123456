"""R1 契约测试：Agent Registry + BaseAgent 能力声明 + PromptProvider 解析。

验收口径（R1 实施报告）：

1. **Registry 单一真值源**：7 个业务 agent_id 全部可发现，``capabilities`` 非空，
   ``prompt_id`` 均可被 PromptProvider 解析；
2. **agent_id 与 adapter.agent_name 分离**：业务规范 id（Prompt 表 / 11.2）与
   BaseAgent 内部名（历史值 ``learning_diagnosis`` 等）分别可查，历史值不改；
3. **BaseAgent 新字段**类型校验生效、``manifest()`` 结构完整；
4. **PromptProvider 三段路由**：DB 优先 → 代码兜底 → LLMClient 归属；渲染结果与
   改造前 ``render_template`` 逐字一致；``services.prompts.AGENT_IDS`` 仍为 3 项
   （守住 ``/admin/prompts/{agentId}`` 的可接受 id 集合不被 R1 改变）；
5. **run_agent 契约不变**：``{agentId,name,prompt,output}``，未知 agentId 仍抛
   ``未知 agentId``；
6. **新增 3 个 Adapter**（planner/dialogue/feynman）可执行且输出符合 Pydantic Schema。

本文件零网络、零计费（conftest 强制 mock provider + 临时隔离库）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agents import (
    dialogue_agent,
    feynman_agent,
    planner_agent,
    registry as agent_registry,
    run_agent,
)
from app.agents.adapters import (
    DialogueAgentAdapter,
    FeynmanAgentAdapter,
    PathPlanningAgentAdapter,
)
from app.agents.prompts.provider import get_prompt_provider, render_template
from app.core import llm as llm_mod
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.services import prompts as prompts_service

BUSINESS_AGENT_IDS = (
    "diagnosis",
    "generation",
    "critic",
    "planner",
    "dialogue",
    "feynman",
    "evaluation",
)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # 触发 lifespan：建表 + 幂等种子
        yield c


@pytest.fixture()
def db(client):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolated_mock_provider():
    """本模块测试与执行顺序无关：确保走 mock 单例，结束后原样还原。"""
    previous_provider = settings.llm_provider
    previous_client = llm_mod._client
    settings.llm_provider = "mock"
    llm_mod._client = None
    yield
    settings.llm_provider = previous_provider
    llm_mod._client = previous_client


# ── 1. Registry：单一真值源 ───────────────────────────────────────────────────


def test_registry_exposes_all_seven_business_agents():
    assert agent_registry.available() == tuple(sorted(BUSINESS_AGENT_IDS))


def test_every_agent_declares_capabilities_tools_and_prompt():
    specs = agent_registry.specs()
    assert len(specs) == len(BUSINESS_AGENT_IDS)
    for spec in specs:
        # 能力非空且全为非空白字符串 —— 分发层的硬约束
        assert spec.capabilities, f"{spec.agent_id} 未声明 capabilities"
        assert all(cap.strip() for cap in spec.capabilities), spec.agent_id
        # 工具可以为空（R1 尚未接 ToolRegistry），但必须是 tuple[str,...]
        assert isinstance(spec.tools, tuple)
        assert all(tool.strip() for tool in spec.tools), spec.agent_id
        assert spec.prompt_id, f"{spec.agent_id} 未声明 prompt_id"
        assert spec.has_entrypoint, f"{spec.agent_id} 缺少单一执行入口"
        assert spec.name.strip() and spec.description.strip()
        assert spec.input_schema is not None and spec.output_schema is not None


def test_capabilities_of_and_tools_of():
    caps = agent_registry.capabilities_of("planner")
    assert "path_planning" in caps
    # R4.2：critic 已声明 Tool 能力（最小权限，比 generation 少 learning/path）
    critic_tools = agent_registry.tools_of("critic")
    assert "memory/search" in critic_tools
    assert "learning/path" not in critic_tools
    # generation 拥有全部 4 个
    generation_tools = agent_registry.tools_of("generation")
    assert {"memory/search", "learning/profile", "learning/mastery", "learning/path"} <= set(
        generation_tools
    )
    # 未授权的 Agent 仍无工具（最小权限）
    assert agent_registry.tools_of("planner") == ()
    assert agent_registry.tools_of("dialogue") == ()


def test_unknown_agent_raises_keyerror():
    with pytest.raises(KeyError, match="unknown agent"):
        agent_registry.get("not-an-agent")


def test_duplicate_register_rejected_unless_replace():
    spec = agent_registry.get("diagnosis")
    with pytest.raises(ValueError, match="already registered"):
        agent_registry.register(spec)
    # replace=True 允许覆盖（幂等，覆盖的是同一对象）
    assert agent_registry.register(spec, replace=True) is spec


def test_register_rejects_empty_capabilities():
    with pytest.raises(ValueError, match="capabilities"):
        agent_registry.register(
            agent_registry.AgentSpec(agent_id="bad", name="bad")
        )


def test_registry_reseeds_after_clear():
    agent_registry.clear()
    assert agent_registry.available() == tuple(sorted(BUSINESS_AGENT_IDS))


def test_manifest_shape():
    manifests = agent_registry.manifests()
    assert len(manifests) == len(BUSINESS_AGENT_IDS)
    for item in manifests:
        assert set(item) == {
            "agentId",
            "name",
            "description",
            "capabilities",
            "tools",
            "promptId",
            "hasEntrypoint",
            "entrypointName",
            "version",
        }
        assert isinstance(item["capabilities"], list) and item["capabilities"]


def test_canonical_id_is_distinct_from_legacy_adapter_name():
    """业务规范 id 与 BaseAgent 历史名不同 —— 前者是 Prompt/11.2 真值，后者不改。"""
    expected = {
        "diagnosis": "learning_diagnosis",
        "generation": "resource_generation",
        "critic": "quality_critic",
        "evaluation": "evaluation",
        "planner": "path_planning",
        "dialogue": "dialogue",
        "feynman": "feynman",
    }
    for agent_id, entrypoint_name in expected.items():
        spec = agent_registry.get(agent_id)
        assert spec.agent_id == agent_id
        assert spec.manifest()["entrypointName"] == entrypoint_name


# ── 2. BaseAgent 新字段 ───────────────────────────────────────────────────────


def test_adapter_manifest_and_capability_declaration(db):
    adapter = PathPlanningAgentAdapter(db, trace_id="trace-r1-plan")
    manifest = adapter.manifest()
    assert manifest["agentId"] == "path_planning"
    assert manifest["promptId"] == "planner"
    assert "prereq_closure" in manifest["capabilities"]
    assert manifest["tools"] == []
    assert manifest["traceId"] == "trace-r1-plan"
    assert manifest["inputSchema"] == "PathPlanningInput"
    assert manifest["outputSchema"] == "PathPlanningOutput"


def test_base_agent_rejects_malformed_capability_declaration():
    from app.agents.base import BaseAgent

    class _BadAgent(BaseAgent):
        agent_name = "bad"
        description = "bad"
        input_schema = PathPlanningAgentAdapter.input_schema
        output_schema = PathPlanningAgentAdapter.output_schema
        capabilities = ["not-a-tuple"]  # list 而非 tuple → TypeError

        def execute(self, agent_input, state):  # pragma: no cover
            return {}

    with pytest.raises(TypeError, match="capabilities"):
        _BadAgent()


# ── 3. PromptProvider：DB 优先 / 代码兜底 / LLMClient 归属 ───────────────────


def test_prompt_agent_ids_remain_three():
    """守住 /admin/prompts/{agentId} 的可接受 id 集合：R1 不得扩张。"""
    assert prompts_service.AGENT_IDS == ("diagnosis", "generation", "critic")


def test_prompt_provider_prefers_db_and_is_hot_reloadable(db):
    provider = get_prompt_provider()
    for agent_id in prompts_service.AGENT_IDS:
        resolved = provider.resolve(db, agent_id)
        assert resolved.source == "db", agent_id
        assert resolved.available is True
        assert resolved.hot_reloadable is True
        assert resolved.version is not None
        assert resolved.template
        assert resolved.owner == "services.prompts.PromptTemplate"


def test_prompt_provider_declares_llm_client_owned_agents(db):
    """planner/dialogue/feynman/evaluation 只登记归属，不参与模板渲染。"""
    provider = get_prompt_provider()
    for agent_id in ("planner", "dialogue", "feynman", "evaluation"):
        resolved = provider.resolve(db, agent_id)
        assert resolved.source == "llm_client", agent_id
        assert resolved.available is False
        assert resolved.owner and "core.llm.LLMClient" in resolved.owner
        assert resolved.name
        with pytest.raises(ValueError, match="不可模板渲染"):
            resolved.render({})


def test_prompt_provider_falls_back_to_code_on_db_error(db, monkeypatch):
    """DB 故障 → 回落代码侧默认模板，渲染可用（热更失效＝回默认，而不是失败）。"""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated prompt table outage")

    monkeypatch.setattr(prompts_service, "get_prompt", _boom)
    resolved = get_prompt_provider().resolve(db, "generation")
    assert resolved.source == "code"
    assert resolved.available is True
    assert resolved.db_error == "RuntimeError"
    assert resolved.owner == "services.prompts._DEFAULTS"

    name, template, variables = prompts_service.get_default_prompt("generation")
    assert resolved.name == name
    payload = {
        "kpName": "神经网络",
        "description": "核心概念",
        "difficulty": "初级",
        "ragContext": "[1] 证据",
        "learnerProfile": "零基础",
    }
    rendered = resolved.render(payload)
    assert rendered == render_template(template, payload)
    assert rendered != template  # 占位符确实被替换


def test_prompt_provider_unknown_id_is_not_renderable(db):
    resolved = get_prompt_provider().resolve(db, "no-such-agent")
    assert resolved.source == "unknown"
    assert resolved.available is False


def test_get_default_prompt_contract():
    assert prompts_service.get_default_prompt("nope") is None
    entry = prompts_service.get_default_prompt("diagnosis")
    assert entry is not None
    name, template, variables = entry
    assert name == "学情诊断Agent"
    assert set(variables) == {"profileSummary", "masteryStatus", "targetJob"}
    assert "{profileSummary}" in template


# ── 4. render_template 行为（改造前后一致）────────────────────────────────────


def test_render_template_replaces_only_known_placeholders():
    assert render_template("a {x} b {y}", {"x": 1}) == "a 1 b {y}"
    assert render_template("{a}{a}", {"a": "z"}) == "zz"
    assert render_template("no placeholder", {}) == "no placeholder"
    # 普通花括号（如 JSON 示例）不得触发 KeyError
    assert render_template('{"k": 1} {v}', {"v": 2}) == '{"k": 1} 2'


# ── 5. run_agent 契约不变 ─────────────────────────────────────────────────────


def test_run_agent_returns_legacy_contract(db):
    result = run_agent(
        db,
        "diagnosis",
        {
            "profileSummary": "六维画像：基础一般",
            "masteryStatus": '{"nn": "weak"}',
            "targetJob": "大模型应用工程师",
            "kpId": "nn",
            "kpName": "神经网络",
        },
    )
    assert set(result) == {"agentId", "name", "prompt", "output"}
    assert result["agentId"] == "diagnosis"
    assert result["name"] == "学情诊断Agent"
    assert "六维画像：基础一般" in result["prompt"]
    assert "{profileSummary}" not in result["prompt"]  # 占位符已替换
    assert isinstance(result["output"], dict)


def test_run_agent_unknown_id_raises_legacy_message(db):
    with pytest.raises(ValueError, match="未知 agentId"):
        run_agent(db, "no-such-agent", {})


# ── 6. 新增 3 个 Adapter ───────────────────────────────────────────────────────


def test_path_planning_adapter_delegates_without_logic_copy(monkeypatch):
    captured: dict = {}

    def _fake_plan_path(db, *, user_id, target_job_id=None, narrate=True):
        captured.update(
            db=db, user_id=user_id, target_job_id=target_job_id, narrate=narrate
        )
        return {"lessons": [{"sequence": 1, "topic": "神经网络"}], "summary": "摘要"}

    monkeypatch.setattr(planner_agent, "plan_path", _fake_plan_path)
    db = object()
    message = PathPlanningAgentAdapter(db, trace_id="trace-r1-plan").run(
        task_id="task-r1-plan",
        agent_input={"user_id": "u1", "target_job_id": "j1", "narrate": False},
    )
    assert captured == {
        "db": db,
        "user_id": "u1",
        "target_job_id": "j1",
        "narrate": False,
    }
    assert message.output["summary"] == "摘要"
    assert message.output["lessons"][0]["topic"] == "神经网络"
    assert message.metadata["traceId"] == "trace-r1-plan"


def test_path_planning_adapter_rejects_missing_user_id():
    with pytest.raises(ValidationError):
        PathPlanningAgentAdapter(object()).run(task_id="t", agent_input={})


def test_dialogue_adapter_dispatches_operation(monkeypatch):
    def _fake_first_question():
        return "先随便聊聊——你接触过哪些？"

    monkeypatch.setattr(dialogue_agent, "opening_first_question", _fake_first_question)
    message = DialogueAgentAdapter().run(
        task_id="task-r1-dialogue",
        agent_input={"operation": "opening_question", "payload": {}},
    )
    assert message.output["operation"] == "opening_question"
    assert message.output["result"] == "先随便聊聊——你接触过哪些？"


def test_dialogue_adapter_rejects_unknown_operation():
    with pytest.raises(ValidationError):
        DialogueAgentAdapter().run(
            task_id="t",
            agent_input={"operation": "not-a-real-operation", "payload": {}},
        )


def test_dialogue_adapter_reports_bad_payload_as_value_error(monkeypatch):
    def _fake_classify(dim_key, value):
        return {"key": dim_key, "value": value}

    monkeypatch.setattr(dialogue_agent, "classify_preference", _fake_classify)
    with pytest.raises(ValueError, match="参数不匹配"):
        DialogueAgentAdapter().run(
            task_id="t",
            # classify_preference 不接受 unexpected_kwargs → 应转成可读错误
            agent_input={
                "operation": "classify_preference",
                "payload": {"dim_key": "cognitive_style", "value": "图", "junk": 1},
            },
        )


def test_feynman_adapter_matches_legacy_contract(monkeypatch):
    legacy_payload = {
        "feedback": "讲得不错",
        "gaps": [{"kpId": "nn", "title": "反向传播", "detail": "缺", "severity": "high"}],
        "score": 70,
        "followups": ["再讲讲梯度"],
        "complete": False,
    }

    def _fake_respond(**kwargs):
        return dict(legacy_payload)

    monkeypatch.setattr(feynman_agent, "respond", _fake_respond)
    message = FeynmanAgentAdapter().run(
        task_id="task-r1-feynman",
        agent_input={"kp_id": "nn", "kp_name": "神经网络", "explanation": "我的讲解"},
    )
    assert set(message.output) == {
        "feedback",
        "gaps",
        "score",
        "followups",
        "complete",
    }
    assert message.output["score"] == 70
    assert message.output["gaps"][0]["severity"] == "high"
    assert message.output["complete"] is False


def test_feynman_adapter_requires_explanation():
    with pytest.raises(ValidationError):
        FeynmanAgentAdapter().run(
            task_id="t", agent_input={"kp_id": "nn", "kp_name": "神经网络"}
        )


def test_adapter_construction_contract_is_uniform():
    """7 个 Adapter 的统一构造规则（R4.5.2 固化，防止再次漂移）。

    调查结论（R4.5.2 §4 情况 B）：

    - ``BaseAgent.__init__`` 是 **keyword-only**（R1 设计，``test_framework`` 已断言）；
    - **需要 Session 的 5 个**显式覆写 ``__init__(self, db)``，接受位置参数；
    - **无状态的 2 个**（dialogue / feynman）不覆写 → 无参构造。

    早期测试误写成 ``DialogueAgentAdapter(object())`` —— 给**无状态** Adapter 传
    位置参数，触发 ``TypeError: BaseAgent.__init__() takes 1 positional argument
    but 2 were given``。**不是 Adapter 缺实现，而是测试违反了基类的 keyword-only 契约。**
    """
    import inspect

    from app.agents.adapters import (
        CriticAgentAdapter,
        EvaluationAgentAdapter,
        LearningDiagnosisAgentAdapter,
        ResourceGenerationAgentAdapter,
    )
    from app.agents.base import BaseAgent

    session_adapters = (
        LearningDiagnosisAgentAdapter,
        ResourceGenerationAgentAdapter,
        EvaluationAgentAdapter,
        CriticAgentAdapter,
        PathPlanningAgentAdapter,
    )
    stateless_adapters = (DialogueAgentAdapter, FeynmanAgentAdapter)

    # 基类保持 keyword-only（R1 契约）
    base_params = inspect.signature(BaseAgent.__init__).parameters
    assert base_params["trace_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert base_params["logger"].kind is inspect.Parameter.KEYWORD_ONLY

    # 需要 Session 的：显式覆写，接受位置参数 db
    for cls in session_adapters:
        params = list(inspect.signature(cls.__init__).parameters)
        assert params[:2] == ["self", "db"], f"{cls.__name__} 应接受位置参数 db"
        assert cls.__init__ is not BaseAgent.__init__, f"{cls.__name__} 未覆写 __init__"

    # 无状态的：继承 keyword-only，不接受 db
    for cls in stateless_adapters:
        params = inspect.signature(cls.__init__).parameters
        assert "db" not in params, f"{cls.__name__} 无 Session，不应接受 db"
        assert cls.__init__ is BaseAgent.__init__, f"{cls.__name__} 不应覆写 __init__"

    # 两类都能按各自规则构造成功
    assert PathPlanningAgentAdapter(object()) is not None
    assert DialogueAgentAdapter() is not None
    assert FeynmanAgentAdapter() is not None
