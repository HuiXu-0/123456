"""统一记忆接口（R4.1：Memory Foundation）。

## 为什么需要它

R1–R3 结束时，系统的记忆是**散落的**：4 个 service 各自维护一份模块级
``_sessions``（tutor / profile_dialogue / learning_flow / document_chat），
长期数据躺在 6 张表里，**Agent 没有任何统一入口去读它们** ——
这正是架构分析报告里记的「记忆不可被检索 / Agent 无法基于历史调整行为」。

R4.1 把它收敛成**一个 Port**：

```
Agent ──▶ MemoryPort ──┬─ short_term(session_id)  → 会话消息（进程内，TTL 30min）
                        ├─ long_term(user_id, query) → 标准化 Evidence（只读 SQLite）
                        └─ remember(key, value)     → 工作记忆（进程内，TTL）
```

## 三条硬约束（本模块的存在理由）

1. **Agent 不得直接操作 SQLite Session** —— ``LongTermMemory`` 内部自行开合
   Session，Port 上**不暴露**任何 Session 对象；
2. **State 不得存 DB Session** —— ``Evidence`` / ``Message`` 都是可 JSON 序列化的
   Pydantic 模型，可以安全进 LangGraph State 与 checkpoint；
3. **不建新表、不重写业务算法** —— ``long_term`` 只是既有表的**只读投影**。

## 本模块的依赖位置

``interface.py`` 是**叶子模块**：只依赖 stdlib + pydantic，
**不 import 任何同级模块**。因此 ``session_store`` / ``long_term`` 可以安全地
从它取类型，而组合类 ``AgentMemory`` 放在包 ``__init__`` 里 —— 三者不成环。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    """短期记忆中的一条消息。

    与既有 ``history: [{role, content}]`` 契约对齐。**``extra="allow"``**：
    这是**读模型**（把既有会话数据投影出来），既有数据可能带额外键，
    拒绝会导致读取崩溃 —— 与 ``Evidence``（自产、可严格）刻意不同。
    """

    model_config = ConfigDict(extra="allow")

    role: str
    content: str


class Evidence(BaseModel):
    """长期记忆中的一条标准化证据。

    **必须可 JSON 序列化**：不得携带 SQLAlchemy 对象 / Session / 二进制，
    否则无法进 LangGraph State 与 checkpoint（见 ``workflows/state.py`` 硬约束）。

    Attributes:
        kind: 证据类型 —— ``portrait`` / ``mastery`` / ``step`` / ``note`` /
            ``quiz`` / ``event``（与 6 个既有数据源一一对应）。
        user_id: 归属用户（隔离键）。
        subject: 关联对象 —— 通常是 ``kp_id``；画像类为空串或维度 key。
        summary: **人类可读的一行摘要**，也是关键词检索的主匹配面。
        value: 原始字段快照（保持既有数据形状，不做语义改写）。
        observed_at: ISO-8601 时间戳；无时间语义时为 None。
        confidence: 0–1；数据源没有置信信息时取 1.0（**不臆造**）。
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    user_id: str
    subject: str = ""
    summary: str
    value: dict[str, Any] = Field(default_factory=dict)
    observed_at: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    def matches(self, terms: list[str]) -> bool:
        """确定性关键词匹配（大小写不敏感，任一命中即真）。

        R4.1 刻意用**关键词过滤而非向量检索**：不建新表、不加嵌入、零网络、
        mock 无 Key 可跑；语义检索留作后续（需新表 → 属表结构变更，另行评审）。
        """
        if not terms:
            return True
        haystack = " ".join(
            (self.summary, self.subject, self.kind, str(self.value))
        ).lower()
        return any(term.lower() in haystack for term in terms)


@runtime_checkable
class MemoryPort(Protocol):
    """Agent 访问记忆的**唯一入口**。

    实现方（``AgentMemory``）可提供额外方法；本 Protocol 只规定这三个，
    保证「换一个实现，Agent 代码不用改」。
    """

    def short_term(self, session_id: str) -> list[Message]:
        """读取某会话的短期消息（按 sessionId 隔离；过期/未知 → 空列表）。"""
        ...

    def long_term(self, user_id: str, query: str) -> list[Evidence]:
        """读取某用户的长期证据；``query`` 为空 → 全量（受 limit 约束）。"""
        ...

    def remember(self, key: str, value: Any) -> None:
        """写入工作记忆（进程内、TTL，供同一 Agent 后续步骤读取）。"""
        ...
