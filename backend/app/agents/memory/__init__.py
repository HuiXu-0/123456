"""统一记忆入口（R4.1：Memory Foundation）。

``AgentMemory`` 是 ``MemoryPort`` 的**默认实现**，组合三块存储：

```
AgentMemory (MemoryPort)
├── short_term(session_id)   → 跨已登记会话池查找（4 个 service 池 + 工作记忆池）
├── long_term(user_id, query)→ LongTermMemory（只读 6 张既有表）
└── remember(key, value)     → 工作记忆（进程内、TTL、**按用户前缀隔离**）
```

## 为什么组合类放在 ``__init__`` 而不是第 4 个文件

规格指定的 4 个文件是 ``__init__ / interface / session_store / long_term``。
``interface.py`` 必须是**叶子**（``session_store`` / ``long_term`` 都要从它取类型），
若把组合类塞进去会形成 ``interface → long_term → interface`` 的循环。
放在 ``__init__`` 是唯一不成环的位置：它单向依赖三个兄弟模块。

## 记忆分层

| 层 | 存储 | 生命周期 | 隔离键 |
|---|---|---|---|
| 工作记忆 | ``SessionStore("working_memory")`` | 进程内 TTL | ``{user_id}:{key}`` |
| 短期（会话） | 4 个 service 的 ``SessionStore``（经注册表） | 进程内 TTL 30min | ``sessionId`` |
| 长期 | SQLite 6 张既有表（只读） | 持久 | ``user_id`` |

## 安全：``remember`` 的用户隔离

工作记忆是**进程内共享**的，若 ``key`` 不做用户前缀，A 用户可读到 B 的数据。
故 ``for_user(user_id)`` 返回**绑定视图**（共享底层存储、只加前缀），
``remember`` / ``recall`` 一律走绑定前缀 —— **隔离在接口层，不依赖调用方自觉**。
"""
from __future__ import annotations

import time
from typing import Any

from app.agents.memory.interface import Evidence, MemoryPort, Message
from app.agents.memory.long_term import (
    LongTermMemory,
    as_dicts,
    split_terms,
)
from app.agents.memory.session_store import (
    SESSION_TTL_SECONDS,
    SessionStore,
    clear_pools,
    find_session,
    pools,
    register_pool,
)

__all__ = [
    "DEFAULT_WORKING_TTL",
    "AgentMemory",
    "Evidence",
    "LongTermMemory",
    "MemoryPort",
    "Message",
    "SessionStore",
    "as_dicts",
    "clear_pools",
    "find_session",
    "get_memory",
    "pools",
    "register_pool",
    "split_terms",
]

# 工作记忆 TTL：与会话同口径（30 分钟）
DEFAULT_WORKING_TTL = SESSION_TTL_SECONDS


class AgentMemory:
    """``MemoryPort`` 的默认实现。

    可通过 ``for_user(user_id)`` 派生**用户绑定视图**（共享存储、加前缀），
    也可直接实例化（未绑定用户时 ``remember`` 的 key 不加前缀，**仅限内部/测试**）。
    """

    def __init__(
        self,
        *,
        long_term: LongTermMemory | None = None,
        working: SessionStore | None = None,
        user_id: str | None = None,
    ) -> None:
        # ⚠️ R4.5.2：依赖 truthiness 的选择会出错 —— `SessionStore` 继承 `dict`，
        # **空实例为 falsy**，`working or SessionStore(...)` 会把调用方传进来的空存储
        # 错误替换成新实例 → `for_user()` 派生的视图不再共享底层存储
        # （实测 `bound._working is twin._working → False`，工作记忆互相看不见）。
        # 一律改用 **显式 None 判断**：传了就用，没传才创建。
        self._long_term = LongTermMemory() if long_term is None else long_term
        self._working = (
            SessionStore(
                ttl_seconds=DEFAULT_WORKING_TTL,
                name="working_memory",
                id_prefix="w_",
            )
            if working is None
            else working
        )
        self._user_id = (user_id or "").strip() or None

    # ── 元信息 ────────────────────────────────────────────────────────────────

    @property
    def user_id(self) -> str | None:
        return self._user_id

    @property
    def long_term_reader(self) -> LongTermMemory:
        """底层长期记忆读取器（只读；供上层做 limit/kind 定制）。"""
        return self._long_term

    def for_user(self, user_id: str) -> "AgentMemory":
        """派生用户绑定视图：共享底层存储，仅给 ``remember``/``recall`` 加前缀。"""
        uid = (user_id or "").strip()
        if not uid:
            raise ValueError("user_id must not be blank")
        return AgentMemory(
            long_term=self._long_term, working=self._working, user_id=uid
        )

    # ── MemoryPort ────────────────────────────────────────────────────────────

    def short_term(self, session_id: str) -> list[Message]:
        """读取某会话的短期消息（跨已登记池查找）。

        会话不存在 / 已过期 / 历史为空 → 空列表，**不抛错、不隐式创建**。
        脏条目（缺 role/content）会被跳过 —— 这是**读模型**，不该因脏数据崩溃。
        """
        sid = (session_id or "").strip()
        if not sid:
            return []
        found = find_session(sid)
        if found is None:
            return []
        _pool_name, session = found
        history = session.get("history")
        if not isinstance(history, list):
            return []
        messages: list[Message] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role, content = item.get("role"), item.get("content")
            if role is None or content is None:
                continue
            messages.append(Message(role=str(role), content=str(content)))
        return messages

    def long_term(self, user_id: str, query: str) -> list[Evidence]:
        """读取某用户的长期证据（只读既有表；空 query → 全量）。"""
        return self._long_term.query(user_id, query)

    def remember(self, key: str, value: Any) -> None:
        """写入工作记忆（覆盖同 key，TTL 30 分钟，按用户前缀隔离）。

        Raises:
            ValueError: key 为空，或本视图**未绑定用户**却尝试写入
                （防止无意中把数据写进全局命名空间）。
        """
        slot = self._slot(key)
        if self._user_id is None:
            raise ValueError(
                "remember 需要用户绑定：请先 memory.for_user(user_id)，"
                "避免把数据写进未隔离的全局命名空间"
            )
        self._working.gc()
        self._working[slot] = {
            "key": key,
            "value": value,
            "userId": self._user_id,
            "expiresAt": time.monotonic() + self._working.ttl_seconds,
        }

    # ── 工作记忆读取（remember 的读取对偶）────────────────────────────────────

    def recall(self, key: str) -> Any | None:
        """读回 ``remember`` 写入的值；不存在/过期 → None。"""
        slot = self._slot(key)
        self._working.gc()
        entry = self._working.get(slot)
        if not isinstance(entry, dict):
            return None
        # 越权保护：即便 key 猜中，userId 不匹配也不返回
        owner = entry.get("userId")
        if owner is not None and owner != self._user_id:
            return None
        return entry.get("value")

    def forget(self, key: str) -> bool:
        """删除工作记忆条目。"""
        return self._working.pop(self._slot(key), None) is not None

    # ── 只读观测（供 trace / 排查）────────────────────────────────────────────

    def session_pools(self) -> tuple[str, ...]:
        """已登记的会话池名。"""
        return tuple(sorted(pools()))

    def working_keys(self) -> tuple[str, ...]:
        """当前工作记忆的**原始槽位**名（含用户前缀，仅供调试）。"""
        self._working.gc()
        return tuple(sorted(self._working))

    # ── 内部 ──────────────────────────────────────────────────────────────────

    def _slot(self, key: str) -> str:
        text = (key or "").strip()
        if not text:
            raise ValueError("memory key must not be blank")
        if self._user_id is None:
            return text
        return f"{self._user_id}:{text}"


_singleton: AgentMemory | None = None


def get_memory(user_id: str | None = None) -> AgentMemory:
    """进程内单例（与 ``get_llm()`` 同风格）。

    Args:
        user_id: 给定时返回**用户绑定视图**（``remember``/``recall`` 带前缀）；
            不给则返回未绑定的根实例（只有 ``short_term`` / ``long_term`` 可用）。

    Returns:
        ``AgentMemory``（结构上满足 ``MemoryPort``）。
    """
    global _singleton
    if _singleton is None:
        _singleton = AgentMemory()
    if user_id:
        return _singleton.for_user(user_id)
    return _singleton
