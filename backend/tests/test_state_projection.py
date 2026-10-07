"""R2 契约测试：MultiAgentState 分层 + 投影层（trace / legacy state）。

验收口径（R2 实施报告 §4/§6）：

1. **State 十字段**：``MultiAgentState`` 顶层字段与需求完全一致，且**不承载**
   DB Session / Retriever / 密钥等不可序列化对象；
2. **投影保真**：``project_legacy_state`` 产出的 5 个键正是
   ``workflow_runner._snapshot_from_state`` 消费的那 5 个，因此 runner **零改动**；
3. **消息投影**：``project_messages`` 复现迁移前 ``_msg()`` 的键名与顺序，
   **不含 id**（编号仍由两个调用方各自加，与迁移前一致）；
4. **trace 结构**：``build_trace`` 与 11.2 完成态逐字段对齐。

本文件不跑图、不调 LLM —— 纯函数测试，最快暴露投影回归。
"""
from __future__ import annotations

import json

from app.agents.protocol import AgentMessage
from app.core.trace import (
    AGENT_ORDER,
    build_trace,
    display_of,
    make_message,
    project_agents,
    project_legacy_state,
    project_messages,
)
from app.workflows.state import (
    LegacyWorkflowState,
    MultiAgentState,
    append_list,
    merge_dict,
)

# 迁移前 _msg() 的键名与顺序（展示契约，逐字保留）
_LEGACY_MSG_KEYS = ["from", "to", "message", "type", "timestamp"]

# 迁移前 WorkflowState 的字段集合（投影必须覆盖到）
_LEGACY_KEYS = {
    "user_id", "kp_id", "kp_name", "kp_description", "difficulty", "target_job",
    "messages", "profile", "reranked_context", "generated_content",
    "generation_feedback", "validation_result", "hallucination_rate",
    "error_details", "retry_count", "max_retries", "current_node",
    "workflow_status", "final_output", "node_execution_log",
}

# R2 要求的十个顶层字段
_TEN_FIELDS = {
    "identity", "learning_context", "plan", "execution", "evidence",
    "messages", "artifacts", "quality", "errors", "result",
}

# State 禁止承载的关键词（见 workflows/state.py 模块文档）
# R4.5.2：禁止出现在 State **注解里**的类型名。
#
# 原实现用**字段名子串**匹配（`"db" in name.lower()`），被 `generation_feedback`
# 误命中 —— "feed**db**ack" 内部正好含 "db" 这两个连续字符。
# 字段叫什么无所谓，**值的类型**才是"不得携带 Session/ORM/Retriever/Tool"的真实约束，
# 故改为检查注解（`from __future__ import annotations` 下为字符串，未启用时 str() 亦可）。
_FORBIDDEN_TYPES = (
    "Session", "SessionLocal", "Engine", "Connection", "Cursor",
    "Retriever", "BaseTool", "Tool", "Base", "APIClient", "SecretStr",
)


def _state(**overrides) -> MultiAgentState:
    """构造一个带典型运行期取值的 MultiAgentState。"""
    base: MultiAgentState = {
        "identity": {"userId": "u_10001", "workflowId": "wf_abc123", "sessionId": None},
        "learning_context": {
            "kpId": "nn",
            "kpName": "神经网络",
            "kpDescription": "核心概念",
            "difficulty": "初级",
            "targetJob": "大模型应用工程师",
            "profile": {"weakKpIds": ["attention"], "summary": "薄弱"},
        },
        "plan": {"maxRetries": 2},
        "execution": {
            "currentNode": "decision",
            "status": "success",
            "retryCount": 1,
            "nodeLog": [
                {"agent": "diagnosis", "name": "学情诊断Agent", "startedAt": "t0",
                 "endedAt": "t1", "durationMs": 1.0, "retryIndex": 0, "outputSummary": "s"},
            ],
        },
        "evidence": {"ragContext": [{"id": "c1", "content": "证据"}], "references": []},
        "messages": [],
        "artifacts": {"markdown": "# 讲义", "feedback": "补充例子"},
        "quality": {
            "result": {"passed": True, "validationScore": 0.91,
                       "hallucinationRate": 0.09, "issues": [], "action": "accept"},
            "hallucinationRate": 0.09,
        },
        "errors": [{"retryIndex": 0, "score": 0.5, "issues": ["未接地"]}],
        "result": {
            "markdown": "# 讲义", "validationScore": 0.91,
            "hallucinationRate": 0.09, "degraded": False, "retryCount": 1,
        },
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# ── 1. State 设计 ─────────────────────────────────────────────────────────────


def test_multi_agent_state_has_exactly_ten_fields():
    assert set(MultiAgentState.__annotations__) == _TEN_FIELDS


def test_state_carries_no_forbidden_payload():
    """State 禁止承载 DB Session / Retriever / 密钥 / 客户端等对象。

    R4.5.2 修正：原实现按**字段名子串**匹配，`generation_feedback` 因内部含
    连续 "d"+"b" 而误命中禁词 "db"。现改为检查**注解类型名** —— 这才对应
    "禁止携带某类对象"的本意；字段名本身不影响能否进 State。
    """
    checked = 0
    for schema in (MultiAgentState, LegacyWorkflowState):
        for field, annotation in schema.__annotations__.items():
            text = annotation if isinstance(annotation, str) else str(annotation)
            hits = [bad for bad in _FORBIDDEN_TYPES if bad in text]
            assert not hits, (
                f"{schema.__name__}.{field} 注解出现禁止类型 {hits}：{text}"
            )
            checked += 1
    # 两个 State 的全部字段都被扫到（防止遍历写空导致假绿）
    assert checked >= len(MultiAgentState.__annotations__) + len(
        LegacyWorkflowState.__annotations__
    )
    # 回归锚点：曾经误命中的字段必须通过
    assert "generation_feedback" in LegacyWorkflowState.__annotations__


def test_state_is_json_serializable_end_to_end():
    """可序列化是开启 checkpoint 的前提（含消息在内全量 JSON 化）。"""
    state = _state(
        messages=[
            make_message(
                task_id="t1",
                agent_name="学情诊断Agent",
                display_from="用户",
                display_to="学情诊断Agent",
                display_message="发起请求",
                outputs={"summary": "薄弱"},
            )
        ]
    )
    serializable = {
        "identity": state["identity"],
        "learning_context": state["learning_context"],
        "plan": state["plan"],
        "execution": state["execution"],
        "evidence": state["evidence"],
        "messages": [m.model_dump(mode="json") for m in state["messages"]],
        "artifacts": state["artifacts"],
        "quality": state["quality"],
        "errors": state["errors"],
        "result": state["result"],
    }
    assert json.loads(json.dumps(serializable, ensure_ascii=False))["messages"][0]["agent_name"]


def test_reducers_merge_and_append():
    # merge_dict：节点只返回部分子字段，不得抹掉已填字段
    merged = merge_dict({"currentNode": "a", "nodeLog": [1]}, {"retryCount": 2})
    assert merged == {"currentNode": "a", "nodeLog": [1], "retryCount": 2}
    assert merge_dict(None, {"x": 1}) == {"x": 1}
    assert merge_dict({"x": 1}, None) == {"x": 1}
    # append_list：只追加
    assert append_list(None, [1]) == [1]
    assert append_list([1], [2]) == [1, 2]
    assert append_list([], None) == []


# ── 2. 投影到 legacy（workflow_runner 零改动的保证）───────────────────────────


def test_legacy_projection_covers_all_workflow_runner_keys():
    """runner 读 5 个键：current_node / messages / node_execution_log /
    workflow_status / validation_result —— 必须全部对上。"""
    projected = project_legacy_state(_state())
    for key in ("current_node", "messages", "node_execution_log",
                "workflow_status", "validation_result"):
        assert key in projected, key

    assert projected["current_node"] == "decision"
    assert projected["workflow_status"] == "success"
    assert projected["validation_result"]["action"] == "accept"
    assert projected["node_execution_log"][0]["agent"] == "diagnosis"


def test_legacy_projection_key_set_matches_migration_before():
    projected = project_legacy_state(_state())
    assert set(projected) == _LEGACY_KEYS


def test_legacy_projection_scalars_and_nested():
    projected = project_legacy_state(_state())
    assert projected["user_id"] == "u_10001"
    assert projected["kp_id"] == "nn"
    assert projected["kp_name"] == "神经网络"
    assert projected["difficulty"] == "初级"
    assert projected["target_job"] == "大模型应用工程师"
    assert projected["profile"]["weakKpIds"] == ["attention"]
    assert projected["reranked_context"][0]["id"] == "c1"
    assert projected["generated_content"] == "# 讲义"
    assert projected["generation_feedback"] == "补充例子"
    assert projected["hallucination_rate"] == 0.09
    assert projected["retry_count"] == 1
    assert projected["max_retries"] == 2
    assert projected["error_details"][0]["score"] == 0.5
    assert projected["final_output"]["degraded"] is False


def test_legacy_projection_of_empty_state_stays_falsy():
    """首帧（尚无节点执行）必须让 runner 的 `if current_node` 判定为假 ——
    与迁移前"跳过首帧回调"的行为一致。"""
    projected = project_legacy_state({})
    assert not projected["current_node"]
    assert projected["messages"] == []
    assert projected["node_execution_log"] == []
    assert projected["final_output"] is None
    assert projected["profile"] is None
    assert projected["validation_result"] is None


# ── 3. 消息投影 ───────────────────────────────────────────────────────────────


def test_make_message_stores_display_and_real_payload():
    message = make_message(
        task_id="wf_1:diagnostic",
        agent_name="学情诊断Agent",
        display_from="用户",
        display_to="学情诊断Agent",
        display_message="发起「神经网络」学习资源生成请求",
        display_type="request",
        inputs={"kpId": "nn"},
        outputs={"summary": "薄弱"},
    )
    assert isinstance(message, AgentMessage)
    assert message.task_id == "wf_1:diagnostic"
    assert message.agent_name == "学情诊断Agent"
    assert message.input == {"kpId": "nn"}
    assert message.output == {"summary": "薄弱"}
    assert message.metadata["display"] == {
        "from": "用户", "to": "学情诊断Agent",
        "message": "发起「神经网络」学习资源生成请求", "type": "request",
    }


def test_project_messages_key_order_and_no_id():
    """键名与顺序须与迁移前 _msg() 一致；且**不含 id**（编号仍在两个调用方）。"""
    message = make_message(
        task_id="t",
        agent_name="RAG系统",
        display_from="RAG系统",
        display_to="领域知识生成Agent",
        display_message="检索到 3 条相关知识切片",
    )
    [projected] = project_messages([message])
    assert list(projected) == _LEGACY_MSG_KEYS
    assert "id" not in projected
    assert projected == {
        "from": "RAG系统",
        "to": "领域知识生成Agent",
        "message": "检索到 3 条相关知识切片",
        "type": "response",
        "timestamp": message.timestamp.isoformat(),
    }


def test_display_fallback_when_metadata_missing():
    """缺 display 时按执行者兜底，投影永不抛错。"""
    bare = AgentMessage(task_id="t", agent_name="某Agent", output={"a": 1})
    assert display_of(bare)["from"] == "某Agent"
    assert display_of(bare)["to"] == "用户"
    assert display_of(bare)["type"] == "response"
    [projected] = project_messages([bare])
    assert projected["message"] == ""  # 空消息仍产出键，保结构完整


def test_project_messages_preserves_order_across_nodes():
    msgs = [
        make_message(task_id="t", agent_name="A", display_from="用户", display_to="A",
                     display_message=f"m{i}")
        for i in range(4)
    ]
    projected = project_messages(msgs)
    assert [m["message"] for m in projected] == ["m0", "m1", "m2", "m3"]


# ── 4. trace 结构（11.2）──────────────────────────────────────────────────────


def test_build_agents_fixed_three_and_degraded_marks_critic_error():
    node_log = [
        {"agent": "diagnosis", "name": "学情诊断Agent", "outputSummary": "s1"},
        {"agent": "generation", "name": "领域知识生成Agent", "outputSummary": "s2"},
        {"agent": "critic", "name": "内容审核校验Agent", "outputSummary": "s3"},
    ]
    ok = project_agents(node_log, degraded=False)
    assert [a["id"] for a in ok] == list(AGENT_ORDER) == ["diagnosis", "generation", "critic"]
    assert all(a["status"] == "success" for a in ok)
    assert [a["name"] for a in ok] == ["学情诊断Agent", "领域知识生成Agent", "内容审核校验Agent"]

    degraded = project_agents(node_log, degraded=True)
    assert {a["id"]: a["status"] for a in degraded}["critic"] == "error"
    assert {a["id"]: a["status"] for a in degraded}["diagnosis"] == "success"


def test_build_trace_shape_and_numbering():
    node_log = [
        {"agent": "diagnosis", "name": "学情诊断Agent", "outputSummary": "s1"},
        {"agent": "generation", "name": "领域知识生成Agent", "outputSummary": "s2"},
        {"agent": "critic", "name": "内容审核校验Agent", "outputSummary": "s3"},
    ]
    msgs = [
        make_message(task_id="t", agent_name="学情诊断Agent", display_from="用户",
                     display_to="学情诊断Agent", display_message="发起请求",
                     display_type="request"),
        make_message(task_id="t", agent_name="内容审核校验Agent",
                     display_from="内容审核校验Agent", display_to="用户",
                     display_message="校验通过，学习资源已交付"),
    ]
    trace = build_trace(
        workflow_id="wf_x",
        messages=msgs,
        node_log=node_log,
        rag_context=[{"id": "c1"}],
        degraded=False,
    )
    assert set(trace) == {
        "workflowId", "phase", "step", "agents", "messages",
        "nodes", "ragContextUsed", "stats",
    }
    assert trace["workflowId"] == "wf_x"
    assert trace["phase"] == "complete" and trace["step"] == 4
    assert [m["id"] for m in trace["messages"]] == [1, 2]
    assert trace["nodes"] is not node_log  # 不泄漏可变引用
    assert trace["ragContextUsed"] == [{"id": "c1"}]
    assert trace["stats"] == {
        "completedAgents": 3, "messageCount": 2, "progress": 100,
    }
    for m in trace["messages"]:
        assert list(m)[:1] == ["id"]  # id 在前，与迁移前 {"id": i+1, **m} 一致
        assert set(m) == {"id"} | set(_LEGACY_MSG_KEYS)


def test_build_trace_degraded_stats_and_critic_error():
    node_log = [
        {"agent": "diagnosis", "name": "学情诊断Agent", "outputSummary": "s1"},
        {"agent": "generation", "name": "领域知识生成Agent", "outputSummary": "s2"},
        {"agent": "critic", "name": "内容审核校验Agent", "outputSummary": "s3"},
    ]
    trace = build_trace(
        workflow_id="wf_y", messages=[], node_log=node_log,
        rag_context=[], degraded=True,
    )
    assert trace["stats"]["completedAgents"] == 2  # critic 被标 error，不算 success
    assert trace["stats"]["progress"] == 100
