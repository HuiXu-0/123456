"""LangGraph 状态定义（R2：Workflow 迁移）。

## 两套状态，一条单向数据流

```
MultiAgentState（新，权威）──project_legacy_state()──▶ LegacyWorkflowState（旧，展示）
        ▲                                                      │
   图节点只写它                                            on_update / 11.2 快照
```

- **`MultiAgentState`**：图的权威状态，按需求文档 9.3 的分层拆成 10 个顶层字段；
  节点只读它、只返回**增量**。
- **`LegacyWorkflowState`**：11.2 渲染与 `workflow_runner._snapshot_from_state`
  消费的旧扁平结构，**逐字保留原定义**。它不再是图状态，只由投影产出 ——
  这样 `app.services.workflow_runner` 与前端**完全无感**。

## State 禁止承载的四类对象（硬约束）

DB Session / Retriever 实例 / API Key / 任何非 JSON 可序列化对象。

理由：LangGraph 的 checkpoint 会尝试序列化整个 State；一旦混入 Session 或
Retriever，**开启 checkpoint 即崩溃**，且节点无法独立测试、无法重放。
Session 由节点内部局部开合，Retriever 经 `_build_graph(db, retriever)` 闭包注入
（闭包不在 State 里），密钥只在 `LLMClient` / `settings` 内。

## Reducer 约定

| 字段类 | reducer | 语义 |
|---|---|---|
| 嵌套上下文 dict | `merge_dict` | 节点返回**部分**嵌套字段即可，浅合并、不丢已填字段 |
| `messages` / `errors` | `append_list` | 只追加、不覆盖、不改历史 |
| 其余标量/叶子 | 默认（覆盖） | 节点写什么就是什么 |

**为什么嵌套必须用 `merge_dict`**：LangGraph 默认 reducer 是「覆盖」。若
`diagnostic` 节点只返回 `{"execution": {"currentNode": "diagnostic"}}`，
覆盖语义会把 `execution.nodeLog` 整个抹掉。`merge_dict` 让节点可以安全地只写
自己关心的子字段。`nodeLog` 这类**嵌套列表**仍是整体替换（节点返回
`[*已有的, 新增]`），避免"既覆盖又追加"的二义性。
"""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, Optional, TypedDict

from app.agents.protocol import AgentMessage


# ── Reducer ───────────────────────────────────────────────────────────────────


def append_list(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """追加而非覆盖（需求文档 9.3 messages_reducer / error_reducer）。"""
    return (left or []) + (right or [])


def merge_dict(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """嵌套上下文浅合并：节点可只返回部分子字段。"""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


# ── 分层上下文（全 total=False，便于构造零值与部分更新）───────────────────────


class IdentityContext(TypedDict, total=False):
    """只读身份：谁在跑、跑哪一次。"""

    userId: str
    workflowId: str
    sessionId: Optional[str]


class LearningContext(TypedDict, total=False):
    """学习上下文：知识点、难度、岗位、画像快照（诊断写入，生成读取）。

    ``objective`` 是 R3 Supervisor 的**路由输入**（用户这次想干什么的自由文本）。
    旧入口 ``run_learning_workflow`` 不写它 → Supervisor 视为"无意图信号"并走
    fallback，与旧入口默认行为一致。
    """

    kpId: str
    kpName: str
    kpDescription: str
    difficulty: str
    targetJob: str
    profile: dict[str, Any]
    objective: str


class Plan(TypedDict, total=False):
    """控制计划：走哪条子图 + 重试预算。

    ``targetGraph`` / ``routingReason`` / ``routingConfidence`` 由 R3
    ``SupervisorAgent.__call__`` 写入，Router 的条件边据此分发（避免重复调用
    Supervisor，尤其是 L2 开启时会造成多余 LLM 调用）。
    """

    maxRetries: int
    targetGraph: str
    routingReason: str
    routingConfidence: float


class Execution(TypedDict, total=False):
    """执行控制与过程记录。

    ``routingTrace`` 是 R3.5 的 Supervisor 可观测性记录（input_type / selected_graph /
    reason / confidence / fallback），**只在进程内存在**：``project_legacy_state``
    不投影它，因此**不会进入 11.2 的任何字段**。

    ⚠️ **不要在这里写 ``currentNode`` 的取值来标记 Supervisor**：
    ``workflow_runner._phase_step_active`` 对未知节点名会落到兜底分支返回
    ``("complete", 4)``，导致 ``stats.progress=100`` —— 工作流刚启动就显示"已完成"。
    故 Supervisor 与 pending 节点**都不写 currentNode**（见 R3.5 报告 §3）。
    """

    currentNode: str
    status: str  # pending | running | success | degraded | not_ready
    retryCount: int
    nodeLog: list[dict[str, Any]]
    routingTrace: dict[str, Any]


class Evidence(TypedDict, total=False):
    """证据：检索到的切片与引用。"""

    ragContext: list[dict[str, Any]]
    references: list[dict[str, Any]]


class Artifacts(TypedDict, total=False):
    """产物：生成的讲义与回灌的修订反馈。"""

    markdown: str
    feedback: Optional[str]


class Quality(TypedDict, total=False):
    """质量裁决。"""

    result: dict[str, Any]
    hallucinationRate: Optional[float]


class Result(TypedDict, total=False):
    """最终输出（结构与 11.2 finalOutput 逐字对齐）。"""

    markdown: str
    validationScore: Optional[float]
    hallucinationRate: Optional[float]
    degraded: bool
    retryCount: int
    requiresReview: bool


# ── 图状态 ────────────────────────────────────────────────────────────────────


class MultiAgentState(TypedDict, total=False):
    """资源生成子图的权威状态（需求文档 9.3 分层 + R2 十字段约定）。

    禁止存 DB Session / Retriever 实例 / API Key / 非序列化对象——见模块文档。
    """

    identity: Annotated[IdentityContext, merge_dict]
    learning_context: Annotated[LearningContext, merge_dict]
    plan: Annotated[Plan, merge_dict]
    execution: Annotated[Execution, merge_dict]
    evidence: Annotated[Evidence, merge_dict]
    messages: Annotated[list[AgentMessage], append_list]
    artifacts: Annotated[Artifacts, merge_dict]
    quality: Annotated[Quality, merge_dict]
    errors: Annotated[list[dict[str, Any]], append_list]
    result: Annotated[Result, merge_dict]


class ToolLoopState(MultiAgentState, total=False):
    """R4.3 Tool Loop 状态 = ``MultiAgentState`` + **仅三项**附加字段。

    不另起一套 State（复用现有分层），也不改 ``MultiAgentState`` 本身
    —— 后者被 R2 的 `十字段精确集合` 契约测试锁死。

    Attributes:
        tool_calls: 结构化 Tool Call 记录（append）。
        tool_results: 对应 Observation（append）。
        tool_step: 当前步数，条件边据此强制 ``MAX_TOOL_STEPS=3``。
        tool_decision: 当前待处理决策（覆盖型），条件边据其 ``kind`` 分派。

    ⛔ **禁止**放进 State：DB Session / API Key / ORM 对象 / Tool 实例
    （与模块文档第 4 条一致，否则 checkpoint 必然失败）。
    """

    tool_calls: Annotated[list[dict[str, Any]], append_list]
    tool_results: Annotated[list[dict[str, Any]], append_list]
    tool_step: int
    # 当前待处理决策（覆盖型）：条件边据此分派 tool_execute / final / 预算耗尽
    tool_decision: dict[str, Any]


# ── 旧结构（仅作投影目标，定义逐字保留）───────────────────────────────────────


class LegacyWorkflowState(TypedDict, total=False):
    """B5-a 的扁平 WorkflowState——11.2 渲染与 workflow_runner 的消费契约。

    **不再是图状态**，仅由 ``app.core.trace.project_legacy_state`` 产出。
    定义与迁移前的 ``learning_workflow.WorkflowState`` 逐字一致，
    以保证 ``workflow_runner._snapshot_from_state`` 与既有测试零改动。
    """

    # ========== 输入数据 ==========
    user_id: str
    kp_id: str
    kp_name: str
    kp_description: str
    difficulty: str
    target_job: str

    # ========== 消息历史（11.2 messages，追加） ==========
    messages: Annotated[list[dict], append_list]

    # ========== 学情诊断阶段 ==========
    profile: Optional[dict]

    # ========== RAG 检索阶段 ==========
    reranked_context: Optional[list[dict]]

    # ========== 内容生成阶段 ==========
    generated_content: Optional[str]
    generation_feedback: Optional[str]

    # ========== 内容校验阶段 ==========
    validation_result: Optional[dict]
    hallucination_rate: Optional[float]
    error_details: Annotated[list[dict], append_list]

    # ========== 流程控制 ==========
    retry_count: int
    max_retries: int
    current_node: str
    workflow_status: str  # pending|running|success|degraded

    # ========== 输出结果 ==========
    final_output: Optional[dict]

    # ========== 元数据 ==========
    node_execution_log: Annotated[list[dict], add]


# 迁移前的类型名，供 ``app.workflows.learning_workflow.WorkflowState`` 继续导出
# （``app.services.workflow_runner`` 与既有测试依赖该名字）。
WorkflowState = LegacyWorkflowState
