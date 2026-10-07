"""R4.5 契约测试：Tool Runtime 收尾与回归锁。

验收口径（R4.5 实施报告 §4），逐条对应规格 §6 的 **5 个锁定点**：

1. **ablation 永远关闭 Tool Loop** —— 不依赖 `.env` / 环境默认值
2. `AGENT_TOOL_LOOP_ENABLED=false` 时行为与 R4.4 前一致
3. **Tool Loop 开关不会污染既有消融脚本**（配置/指标/输出格式不变）
4. **Tool Loop trace 不进 11.2**
5. **generation / critic 权限集合没有扩大**

另含第二部分的**依赖兼容性实测**（langgraph 图 API / langchain-core schema），
在测试服务器上真正跑一次，代替"理论上兼容"。

零网络、零计费（conftest 强制 mock provider + 临时隔离库）。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.agents import langchain_adapter
from app.agents import registry as agent_registry
from app.agents.tools import MEMORY_SEARCH_TOOL, get_tool_registry
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.trace import build_trace, project_legacy_state
from app.main import app
from app.workflows.learning_workflow import run_learning_workflow
from app.workflows.nodes import resource_generation as nodes
from app.workflows.nodes import tool_loop as tool_loop_nodes

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ABLATION_PATH = _BACKEND_ROOT / "scripts" / "metrics" / "ablation.py"

# R4.2/R4.3 定的最小权限集 —— **本阶段不得扩大**
EXPECTED_TOOLS = {
    "generation": {
        "memory/search", "learning/profile", "learning/mastery",
        "learning/path", "trusted_rag",
    },
    "critic": {"memory/search", "learning/profile", "learning/mastery", "trusted_rag"},
}
DISABLED_AGENTS = ("diagnosis", "planner", "dialogue", "feynman", "evaluation")

_LEGACY_RESULT_KEYS = {"workflowId", "finalOutput", "trace"}
_FINAL_OUTPUT_KEYS = {
    "markdown", "validationScore", "hallucinationRate", "degraded", "retryCount",
}
_TRACE_KEYS = {
    "workflowId", "phase", "step", "agents", "messages",
    "nodes", "ragContextUsed", "stats",
}
# 迁移前 LegacyWorkflowState 的 20 个键（R2 契约）
_LEGACY_STATE_KEYS = {
    "user_id", "kp_id", "kp_name", "kp_description", "difficulty", "target_job",
    "messages", "profile", "reranked_context", "generated_content",
    "generation_feedback", "validation_result", "hallucination_rate",
    "error_details", "retry_count", "max_retries", "current_node",
    "workflow_status", "final_output", "node_execution_log",
}


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
def _flag_isolation():
    """每条用例从 flag 关闭起步，结束后还原（R4.5 消融隔离会写这个值）。"""
    original = settings.agent_tool_loop_enabled
    settings.agent_tool_loop_enabled = False
    nodes.clear_tool_loop_trace()
    yield
    settings.agent_tool_loop_enabled = original
    nodes.clear_tool_loop_trace()


def _load_ablation():
    """按文件路径加载消融脚本（``scripts/metrics`` 无 ``__init__.py``）。"""
    spec = importlib.util.spec_from_file_location("ablation_under_test", _ABLATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ══════════════════════════════════════════════════════════════════════════
# 1 / 3. 消融隔离：永远关闭 Tool Loop，且不污染实验契约
# ══════════════════════════════════════════════════════════════════════════


def test_ablation_forces_tool_loop_off(db, monkeypatch):
    """**核心断言**：即便环境把开关打开，消融调用工作流时它必须是 False。"""
    ablation = _load_ablation()
    settings.agent_tool_loop_enabled = True  # 人为打开，模拟"环境默认被改过"

    observed: list[bool] = []

    def _spy(*_args, **kwargs):
        # 记录**调用瞬间**的开关状态
        observed.append(settings.agent_tool_loop_enabled)
        return {"finalOutput": {"markdown": "# t", "degraded": False, "retryCount": 0}}

    monkeypatch.setattr(ablation, "run_learning_workflow", _spy)
    monkeypatch.setattr(ablation, "_retrieve_for_lecture", lambda *_a: [{"content": "c"}])
    monkeypatch.setattr(
        ablation, "sentence_grounding",
        lambda *_a, **_k: {"hallucinationRate": 0.1, "totalSentences": 1},
    )

    row = ablation._generate(
        db, "u_10001", "nn", "神经网络", "初级",
        {"retriever": ablation._retrieve_for_lecture,
         "disable_critic": False, "max_retries": 2},
    )

    assert observed and observed[0] is False   # ← 消融绝不带 Tool Loop 跑
    assert settings.agent_tool_loop_enabled is False
    assert row["hallucinationRate"] == 0.1      # 指标计算未受影响


def test_ablation_contract_unchanged():
    """开关隔离不得改动实验算法 / 变量 / 输出格式。"""
    ablation = _load_ablation()

    # 四个消融配置（原实验变量）
    assert set(ablation.CONFIGS) == {"full", "-RAG", "-critic", "-retry"}
    assert set(ablation.CONFIGS["full"]) == {"retriever", "disable_critic", "max_retries"}
    # 实验范围未变
    assert ablation.AI_KPS == ["ml", "nn", "dl", "cnn", "transformer", "finetune"]
    assert ablation.DIFFICULTIES == ["入门", "初级", "高级"]


def test_ablation_generate_output_shape_unchanged(db, monkeypatch):
    """``_generate`` 的输出字段集合 = 接入前原格式（Tool Loop 未新增任何字段）。"""
    ablation = _load_ablation()
    monkeypatch.setattr(ablation, "run_learning_workflow", lambda *a, **k: {
        "finalOutput": {"markdown": "# t", "degraded": True, "retryCount": 2}
    })
    monkeypatch.setattr(ablation, "_retrieve_for_lecture", lambda *_a: [{"content": "c"}])
    monkeypatch.setattr(
        ablation, "sentence_grounding", lambda *_a, **_k: {"hallucinationRate": 0.42, "totalSentences": 7}
    )

    row = ablation._generate(
        db, "u_10001", "nn", "神经网络", "高级",
        {"retriever": ablation._retrieve_for_lecture,
         "disable_critic": True, "max_retries": 0},
    )
    assert set(row) == {
        "kpId", "difficulty", "hallucinationRate",
        "totalSentences", "degraded", "retryCount",
    }
    assert row["hallucinationRate"] == 0.42
    assert row["totalSentences"] == 7
    assert row["degraded"] is True and row["retryCount"] == 2


def test_ablation_script_content_keeps_isolation():
    """源码级锁：隔离代码必须真实存在（防止后续被误删）。"""
    source = _ABLATION_PATH.read_text(encoding="utf-8")
    assert "settings.agent_tool_loop_enabled = False" in source
    assert source.count("settings.agent_tool_loop_enabled = False") >= 2  # 入口 + 每次生成


# ══════════════════════════════════════════════════════════════════════════
# 2. flag=false 行为与 R4.4 前一致
# ══════════════════════════════════════════════════════════════════════════


def test_flag_defaults_to_off():
    assert settings.agent_tool_loop_enabled is False


def test_flag_off_keeps_pre_r44_contract(db, monkeypatch):
    """OFF：返回结构 = R2 以来的契约，且完全不触碰 ToolLoop。"""
    from app.agents.tool_loop import ToolLoop

    def _boom(*_a, **_k):
        raise AssertionError("flag OFF 时不得触碰 ToolLoop")

    monkeypatch.setattr(ToolLoop, "run", _boom)
    assert nodes.tool_loop_trace() == []

    result = run_learning_workflow(db, user_id="u_10001", kp_id="nn", difficulty="初级")

    assert set(result) == _LEGACY_RESULT_KEYS
    assert set(result["finalOutput"]) == _FINAL_OUTPUT_KEYS
    assert set(result["trace"]) == _TRACE_KEYS
    assert nodes.tool_loop_trace() == []  # 内部 trace 也是空的
    assert result["finalOutput"]["degraded"] is False


# ══════════════════════════════════════════════════════════════════════════
# 4. Tool Loop trace 不进 11.2
# ══════════════════════════════════════════════════════════════════════════


def test_projected_legacy_state_has_no_tool_loop_keys():
    """投影回 legacy 的 20 键里不含任何 Tool Loop 字段。"""
    projected = project_legacy_state({
        "execution": {
            "currentNode": "generation",
            "status": "running",
            "retryCount": 0,
            "nodeLog": [],
        },
        "messages": [],
    })
    assert set(projected) == _LEGACY_STATE_KEYS
    text = json.dumps(projected, ensure_ascii=False, default=str)
    for marker in ("toolLoop", "toolSteps", "toolCalls", "truncated", "fallbackReason"):
        assert marker not in text


def test_build_trace_has_no_tool_loop_keys():
    trace = build_trace(
        workflow_id="wf_x", messages=[], node_log=[], rag_context=[], degraded=False
    )
    assert set(trace) == _TRACE_KEYS
    assert "toolLoop" not in json.dumps(trace, ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════════════
# 5. generation / critic 权限集合没有扩大
# ══════════════════════════════════════════════════════════════════════════


def test_tool_permission_sets_not_expanded():
    """**精确集合相等** —— 多一个少一个都算扩权/缩权。"""
    for agent_id, expected in EXPECTED_TOOLS.items():
        actual = set(agent_registry.tools_of(agent_id))
        assert actual == expected, f"{agent_id} 权限变了：{sorted(actual)} != {sorted(expected)}"

    for agent_id in DISABLED_AGENTS:
        assert agent_registry.tools_of(agent_id) == (), f"{agent_id} 不应拥有工具"

    # Agent 总数仍是 7（不新增）
    assert len(agent_registry.available()) == 7


def test_tool_registry_has_no_new_tools():
    """工具总数也锁死：5 个（4 个 R4.2 + trusted_rag 的 Spec 形态）。"""
    specs = {spec.name for spec in get_tool_registry().specs()}
    assert specs == {
        "memory/search", "learning/profile", "learning/mastery",
        "learning/path", "trusted_rag",
    }


# ══════════════════════════════════════════════════════════════════════════
# 第二部分补充：依赖兼容性**实测**（在测试服务器上跑，代替"理论上兼容"）
# ══════════════════════════════════════════════════════════════════════════


def _requirement_line(text: str, name: str) -> str:
    """在 requirements 里找指定依赖的那一行（跳过注释，兼容任意版本约束写法）。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        base = line.split(";")[0].strip()
        for sep in ("~=", "==", ">=", "<=", ">", "<", "[", " "):
            if sep in base:
                base = base.split(sep, 1)[0].strip()
        if base == name:
            return line
    raise AssertionError(f"requirements 中缺少依赖：{name}")


def test_requirements_locks_langgraph_and_langchain():
    """依赖已按决策 A 锁定（配对必须同主版本）。"""
    requirements = (_BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")

    langgraph_line = _requirement_line(requirements, "langgraph")
    langchain_line = _requirement_line(requirements, "langchain-core")
    # 都必须带版本约束（不是裸包名）
    assert any(op in langgraph_line for op in ("~=", "==", ">=")), langgraph_line
    assert any(op in langchain_line for op in ("~=", "==", ">=")), langchain_line


def test_langgraph_resource_generation_graph_compiles(db):
    """实测 langgraph：StateGraph / add_node / add_edge / START / compile 全部可用。"""
    graph = nodes.build_graph(db, nodes._mock_retriever)
    assert graph is not None
    node_names = set(graph.get_graph().nodes)
    for expected in ("diagnostic", "retrieval", "generation", "validation", "decision"):
        assert expected in node_names, f"图中缺少节点 {expected}：{sorted(node_names)}"


def test_langgraph_tool_loop_graph_compiles():
    """实测 langgraph：条件边 + START/END + 独立图编译（R4.3 路径）。"""
    from app.agents.tool_loop import ToolLoop

    loop = ToolLoop(agent_id="generation", actor_id="u_r45")
    graph = tool_loop_nodes.build_tool_loop_graph(loop)
    assert graph is not None
    node_names = set(graph.get_graph().nodes)
    for expected in ("agent_decision", "tool_execute", "final"):
        assert expected in node_names


def test_langchain_tool_schema_uses_adapter():
    """实测 langchain-core：tool schema 产出 OpenAI 标准结构，名字恒等于权限键。"""
    spec = get_tool_registry().spec(MEMORY_SEARCH_TOOL)
    payload = langchain_adapter.tool_schema(spec)

    assert isinstance(langchain_adapter.has_langchain(), bool)
    assert payload.get("type") == "function"
    function = payload.get("function") or {}
    assert function.get("name") == MEMORY_SEARCH_TOOL  # 与白名单校验名一致，不漂移
    assert function.get("description")
    parameters = function.get("parameters") or {}
    assert parameters.get("type") == "object"
    assert "properties" in parameters
    # 成批导出也不抛
    assert langchain_adapter.tool_schemas([spec]) == [payload]


def test_langchain_to_messages_never_raises():
    """实测 langchain-core message 构造：脏数据/版本差异一律降级，不抛。"""
    history = [
        {"kind": "task", "content": "帮我查"},
        {"kind": "tool_call", "tool": "memory/search", "arguments": {"a": 1}, "id": "c1"},
        {"kind": "observation", "id": "c1", "content": "{}"},
        {"kind": "observation", "id": "c2"},   # 缺 content
        "not-a-dict",                          # 脏数据
        None,
    ]
    messages = langchain_adapter.to_messages(history)
    assert isinstance(messages, list)
    if langchain_adapter.has_langchain():
        assert messages  # 可用时至少 task / tool_call 被转换
