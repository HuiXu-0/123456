"""进程内会话存储（R4.1）——把散落在 services 的 4 处 `_sessions` 统一。

## 合并了哪 4 处

| 池名 | 文件 | id 前缀 | 载荷 |
|---|---|---|---|
| ``tutor`` | ``services/tutor.py`` | ``s_`` | sessionId / kpId / history / expiresAt |
| ``dialogue`` | ``services/profile_dialogue.py`` | ``d_`` | sessionId / userId / history / stage / collected / … |
| ``feynman`` | ``services/learning_flow.py`` | ``f_`` | sessionId / kpId / history / expiresAt |
| ``document_chat`` | ``services/document_chat.py`` | ``dc_`` | sessionId / docIds / history / expiresAt |

（``agents/evaluation_agent._NARRATIVE_CACHE`` 是**指纹结果缓存**，不是会话，不并入。）

## 语义**一条都不改**

``SessionStore`` **继承 ``dict``**，原因是既有测试直接戳载荷：

```python
tests/test_b7a.py:273            tutor_service._sessions[sid]["expiresAt"] = 0.0
tests/test_dialogue_profile.py:476  dialogue_service._sessions[sid]["expiresAt"] = 0.0
```

因此：

- **TTL 仍然存放在载荷的 ``expiresAt``**（不是 store 内部另开一套时钟）
  —— 测试把 ``expiresAt`` 写成 0 就能立刻触发过期，与改造前**逐字一致**；
- ``_sessions[sid]`` / ``sid in _sessions`` / ``_sessions.items()`` / ``len()``
  全部照旧可用；
- **sessionId 隔离、TTL 30 分钟、访问时惰性 GC、无后台线程** —— 全部保持。

改变的只有**实现位置**：GC / 续期 / 取建 三段重复代码收敛为一份。

## 池注册表（打破循环导入）

4 个 service 需要 ``SessionStore``（service → memory）；
若 ``memory`` 反过来 import service 去读它们的会话，就成环。
故采用**注册表**：service 导入后**自报**池子，memory 侧只读注册表，
**永不 import services**。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable

# 与 4 个 service 中的常量同值（30 分钟），保持「与 tutor 同口径」的历史约定
SESSION_TTL_SECONDS = 30 * 60

# 载荷构造器：接受新生成的 sid，返回不含 expiresAt 的初始载荷
SessionFactory = Callable[[str], dict[str, Any]]

# 池注册表：池名 → 实例。进程内、随导入建立。
_pools: dict[str, "SessionStore"] = {}


def register_pool(name: str, store: "SessionStore") -> "SessionStore":
    """登记一个会话池（由 service 在模块级调用）。重复登记同名 → 覆盖。"""
    if not name or not name.strip():
        raise ValueError("pool name must not be blank")
    _pools[name] = store
    return store


def pools() -> dict[str, "SessionStore"]:
    """已登记的会话池（供 ``AgentMemory.short_term`` 跨池查找）。"""
    return dict(_pools)


def clear_pools() -> None:
    """清空注册表（仅供测试；service 重新 import 时会再次登记）。"""
    _pools.clear()


class SessionStore(dict):
    """带统一 TTL / GC / 取建逻辑的会话存储。

    继承 ``dict`` 是**刻意的兼容设计**（见模块文档）：既有测试与 service 代码
    都按 dict 语义访问载荷，改成自定义容器会静默破坏它们。
    """

    def __init__(
        self,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        *,
        name: str = "sessions",
        id_prefix: str = "s_",
    ) -> None:
        super().__init__()
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if not id_prefix:
            raise ValueError("id_prefix must not be blank")
        self._ttl_seconds = int(ttl_seconds)
        self._name = name
        self._id_prefix = id_prefix

    # ── 元信息 ────────────────────────────────────────────────────────────────

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    @property
    def name(self) -> str:
        return self._name

    @property
    def id_prefix(self) -> str:
        return self._id_prefix

    # ── TTL / GC（语义与改造前逐字一致）──────────────────────────────────────

    def gc(self) -> int:
        """惰性清理过期会话（访问时触发，无后台线程）。返回清理条数。

        过期判定读的是**载荷里的 ``expiresAt``**（monotonic 时间戳）。
        载荷缺该键时按"刚创建"处理 —— 保留而非误删，避免丢数据。
        """
        now = time.monotonic()
        expired = [
            sid
            for sid, value in list(self.items())
            if float(value.get("expiresAt", now)) < now
        ]
        for sid in expired:
            self.pop(sid, None)
        return len(expired)

    # 旧名兼容（既有 service 内部曾叫 _gc）
    _gc = gc

    def refresh(self, session_id: str) -> bool:
        """续期一个已存在的会话；不存在 → False。"""
        session = self.get(session_id)
        if not isinstance(session, dict):
            return False
        session["expiresAt"] = time.monotonic() + self._ttl_seconds
        return True

    # ── 取 / 建（4 处 _get_session 的统一实现）────────────────────────────────

    def get_or_create(
        self,
        session_id: str | None,
        factory: SessionFactory,
        *,
        id_prefix: str | None = None,
    ) -> dict[str, Any]:
        """取/建会话并续期。过期或未知 id → 以该 id（或新 id）重建空上下文。

        与改造前的 ``_get_session`` 语义逐条对应：

        1. 先 GC（访问时惰性清理）；
        2. ``session_id`` 存在且未过期 → 续期并返回**同一个对象**（引用语义不变，
           调用方就地改 ``history`` 仍然生效）；
        3. 否则用 ``session_id``（若给了）或 ``{id_prefix}{hex10}`` 重建。

        Args:
            session_id: 调用方传入的会话 id，可为 None。
            factory: 接收新 sid、返回初始载荷（**不含** ``expiresAt``，由本方法补）。
            id_prefix: 覆盖本池默认前缀（各 service 用各自的历史前缀）。

        Returns:
            会话载荷字典（已在存储中，调用方可就地修改）。
        """
        self.gc()
        if session_id and session_id in self:
            session = self[session_id]
            session["expiresAt"] = time.monotonic() + self._ttl_seconds
            return session
        prefix = id_prefix or self._id_prefix
        sid = session_id or f"{prefix}{uuid.uuid4().hex[:10]}"
        session = dict(factory(sid))
        session["expiresAt"] = time.monotonic() + self._ttl_seconds
        self[sid] = session
        return session

    # ── 供 MemoryPort 读取 ────────────────────────────────────────────────────

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        """读取某会话的 ``history``；不存在/过期 → 空列表（不抛错）。

        读取本身**不续期**也不触发 GC 之外的副作用 —— 只读不该改变 TTL。
        """
        self.gc()
        session = self.get(session_id)
        if not isinstance(session, dict):
            return []
        history = session.get("history")
        return list(history) if isinstance(history, list) else []

    def append_message(
        self, session_id: str, role: str, content: str
    ) -> dict[str, Any] | None:
        """向某会话追加一条消息；会话不存在 → None（**不隐式创建**）。"""
        session = self.get(session_id)
        if not isinstance(session, dict):
            return None
        history = session.setdefault("history", [])
        if isinstance(history, list):
            history.append({"role": role, "content": content})
        return session

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """浅拷贝当前全部会话（调试 / 测试用）。"""
        return {sid: dict(value) for sid, value in self.items()}


def find_session(session_id: str) -> tuple[str, dict[str, Any]] | None:
    """跨已登记池按 sessionId 查找会话，返回 ``(池名, 载荷)``。

    4 个池的 id 前缀互不相同（``s_`` / ``d_`` / ``f_`` / ``dc_``），
    因此跨池查找是**无歧义**的。
    """
    if not session_id:
        return None
    for name, store in _pools.items():
        if session_id in store:
            session = store.get(session_id)
            if isinstance(session, dict):
                return name, session
    return None
