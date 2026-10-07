"""R4.1 契约测试：Memory Foundation（MemoryPort / SessionStore / LongTermMemory）。

验收口径（R4.1 实施报告 §3–§7）：

1. **MemoryPort 是唯一入口** —— ``AgentMemory`` 结构上满足 Protocol，
   且**不暴露任何 DB Session**；
2. **SessionStore 合并 4 处 `_sessions` 而语义不变** —— sessionId 隔离、
   TTL 30min、惰性 GC、引用语义（复用同一对象）、**既有测试的戳法
   ``_sessions[sid]["expiresAt"] = 0.0`` 照旧可用**；
3. **池注册表跨池可读** —— 4 个池前缀互不相同，故 ``find_session`` 无歧义，
   ``AgentMemory.short_term`` 能读到任一 service 的会话；
4. **工作记忆按用户隔离** —— A 写的 ``remember``，B 读不到（越权保护）；
5. **LongTermMemory 只读 6 张既有表、不建新表** —— 产出可 JSON 序列化的
   ``Evidence``，且空 user_id 直接返回空（**绝不跨用户**）。

零网络、零计费（conftest 强制 mock provider + 临时隔离库）。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents.memory import (
    AgentMemory,
    Evidence,
    LongTermMemory,
    MemoryPort,
    Message,
    SessionStore,
    clear_pools,
    find_session,
    get_memory,
    pools,
    split_terms,
)
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import Mastery, QuizAttempt

_USER = "u_r41_memory"


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


# 把 4 个 service import 进来 → 各自模块级 register_pool 执行
@pytest.fixture(scope="module", autouse=True)
def _register_pools():
    import app.services.document_chat  # noqa: F401
    import app.services.learning_flow  # noqa: F401
    import app.services.profile_dialogue  # noqa: F401
    import app.services.tutor  # noqa: F401


@pytest.fixture(scope="module", autouse=True)
def _seed_long_term_evidence(client):
    """一次性播种长期记忆数据（幂等），使各 long_term 用例**与执行顺序无关**。"""
    session = SessionLocal()
    try:
        if session.query(Mastery).filter_by(user_id=_USER).first() is None:
            session.add(
                Mastery(
                    user_id=_USER, kp_id="nn", status="passed",
                    score=88, confidence=0.9, score_source="quiz",
                )
            )
        if session.query(QuizAttempt).filter_by(user_id=_USER).first() is None:
            session.add(
                QuizAttempt(
                    user_id=_USER, kp_id="nn", score=90,
                    correct_count=9, total=10, passed=True,
                )
            )
        session.commit()
    finally:
        session.close()


# ── 1. MemoryPort ────────────────────────────────────────────────────────────


def test_agent_memory_satisfies_memory_port():
    memory = AgentMemory()
    assert isinstance(memory, MemoryPort)  # runtime_checkable：结构满足
    for name in ("short_term", "long_term", "remember"):
        assert callable(getattr(memory, name))


def test_memory_port_never_exposes_db_session():
    """Agent 不得直接操作 SQLite Session —— Port 上不出现任何 Session 面。"""
    memory = AgentMemory()
    forbidden = ("db", "session", "Session", "engine", "connection", "cursor")
    for name in forbidden:
        assert not hasattr(memory, name), f"MemoryPort 泄露了 {name}"

    reader = LongTermMemory()
    for name in ("db", "session", "Session", "engine"):
        assert not hasattr(reader, name), f"LongTermMemory 泄露了 {name}"


def test_get_memory_returns_singleton_and_user_bound_view():
    root = get_memory()
    assert get_memory() is root
    assert root.user_id is None

    bound = root.for_user(_USER)
    assert bound.user_id == _USER
    # 派生视图共享底层存储（同一用户两次派生应互通）
    twin = root.for_user(_USER)
    bound.remember("shared", "hello")
    try:
        assert twin.recall("shared") == "hello"
    finally:
        bound.forget("shared")

    with pytest.raises(ValueError, match="blank"):
        root.for_user("  ")


def test_root_memory_cannot_write_working_memory():
    """未绑定用户的根实例禁止写 —— 防止写进无隔离的全局命名空间。"""
    with pytest.raises(ValueError, match="for_user"):
        get_memory().remember("leak", "value")


# ── 2. SessionStore 语义 ─────────────────────────────────────────────────────


def test_session_store_create_reuse_and_rebuild():
    store = SessionStore(ttl_seconds=1, name="t", id_prefix="x_")
    first = store.get_or_create(None, lambda sid: {"sessionId": sid, "history": []})
    assert first["sessionId"].startswith("x_")
    assert "expiresAt" in first and first["history"] == []

    # 同 id → 复用**同一个对象**（引用语义与改造前一致）
    again = store.get_or_create(first["sessionId"], lambda sid: {"sessionId": sid})
    assert again is first

    # dict 兼容：既有测试把 expiresAt 戳成 0 即可强制过期
    store[first["sessionId"]]["expiresAt"] = 0.0
    rebuilt = store.get_or_create(first["sessionId"], lambda sid: {"sessionId": sid})
    assert rebuilt is not first
    assert rebuilt["sessionId"] == first["sessionId"]  # 同 id 重建
    assert len(store) == 1  # 过期的已被 GC


def test_session_store_gc_counts_and_keeps_valid():
    store = SessionStore(ttl_seconds=30, name="t", id_prefix="y_")
    a = store.get_or_create("y1", lambda sid: {"sessionId": sid})
    store.get_or_create("y2", lambda sid: {"sessionId": sid})
    store["y1"]["expiresAt"] = 0.0
    assert store.gc() == 1
    assert "y1" not in store and "y2" in store
    assert store.gc() == 0  # 幂等


def test_session_store_ignores_missing_expires_at():
    """载荷缺 expiresAt 时按"刚创建"处理 —— 保留而非误删，避免丢数据。"""
    store = SessionStore(name="t", id_prefix="z_")
    store["z1"] = {"sessionId": "z1"}
    assert store.gc() == 0
    assert "z1" in store


def test_session_store_validates_config():
    with pytest.raises(ValueError):
        SessionStore(ttl_seconds=0)
    with pytest.raises(ValueError):
        SessionStore(id_prefix="")


def test_session_store_messages_and_append():
    store = SessionStore(name="t", id_prefix="m_")
    sid = store.get_or_create(None, lambda s: {"sessionId": s, "history": []})["sessionId"]
    assert store.messages(sid) == []
    assert store.append_message(sid, "user", "hi") is not None
    assert store.messages(sid) == [{"role": "user", "content": "hi"}]
    # 不存在的会话：读 → 空；写 → None（**不隐式创建**）
    assert store.messages("nope") == []
    assert store.append_message("nope", "user", "x") is None


# ── 3. 4 处 service 迁移：语义逐条保持 ───────────────────────────────────────


def test_four_pools_registered_with_unique_prefixes():
    registered = pools()
    assert {"tutor", "dialogue", "feynman", "document_chat"} <= set(registered)
    prefixes = [store.id_prefix for store in registered.values()]
    # 前缀互不相同 → 跨池按 sessionId 查找无歧义
    assert len(prefixes) == len(set(prefixes))
    assert registered["tutor"].ttl_seconds == 30 * 60


def test_tutor_session_contract_preserved():
    from app.services import tutor as tutor_service

    assert isinstance(tutor_service._sessions, SessionStore)
    assert tutor_service.SESSION_TTL_SECONDS == 30 * 60

    session = tutor_service._get_session(None, "nn")
    assert set(session) == {"sessionId", "kpId", "history", "expiresAt"}
    assert session["kpId"] == "nn" and session["history"] == []
    session["history"].append({"role": "user", "content": "hi"})

    # 引用语义：同 id 返回同一对象
    assert tutor_service._get_session(session["sessionId"], "nn") is session
    # 既有测试的戳法（tests/test_b7a.py:273）
    tutor_service._sessions[session["sessionId"]]["expiresAt"] = 0.0
    rebuilt = tutor_service._get_session(session["sessionId"], "nn")
    assert rebuilt["history"] == []  # 旧上下文失效，仅本轮


def test_tutor_gc_helper_still_works():
    from app.services import tutor as tutor_service

    session = tutor_service._get_session(None, "dl")
    tutor_service._sessions[session["sessionId"]]["expiresAt"] = 0.0
    tutor_service._gc()
    assert session["sessionId"] not in tutor_service._sessions


def test_dialogue_session_contract_preserved():
    from app.services import profile_dialogue as dialogue_service

    assert isinstance(dialogue_service._sessions, SessionStore)
    session = dialogue_service._get_session(None, "user-1")
    assert session["sessionId"].startswith("d_")
    assert session["userId"] == "user-1"
    assert session["stage"]  # 三段式诊断状态机初值仍在
    assert isinstance(session["collected"], set)
    assert "expiresAt" in session

    session["history"].append({"role": "user", "content": "hi"})
    # 既有测试的戳法（tests/test_dialogue_profile.py:476）
    dialogue_service._sessions[session["sessionId"]]["expiresAt"] = 0.0
    rebuilt = dialogue_service._get_session(session["sessionId"], "user-1")
    assert rebuilt["history"] == []


def test_feynman_session_contract_preserved():
    from app.services import learning_flow as learning_service

    assert isinstance(learning_service._sessions, SessionStore)
    session = learning_service._get_session(None, "nn")
    assert session["sessionId"].startswith("f_")
    assert set(session) == {"sessionId", "kpId", "history", "expiresAt"}


def test_document_chat_session_contract_preserved():
    import app.services.document_chat as doc_chat

    assert isinstance(doc_chat._sessions, SessionStore)
    session = doc_chat._get_session(None, ["d1", "d2"])
    assert session["sessionId"].startswith("dc_")
    assert session["docIds"] == ["d1", "d2"]
    assert set(session) == {"sessionId", "docIds", "history", "expiresAt"}


def test_pools_are_separate():
    """四个池互不串味：同一时刻各自持有自己的会话。"""
    from app.services import profile_dialogue as dialogue_service
    from app.services import tutor as tutor_service

    tutor_sid = tutor_service._get_session(None, "nn")["sessionId"]
    dialogue_sid = dialogue_service._get_session(None, "user-1")["sessionId"]

    assert tutor_sid in tutor_service._sessions
    assert dialogue_sid not in tutor_service._sessions
    assert dialogue_sid in dialogue_service._sessions
    assert tutor_sid not in dialogue_service._sessions

    found = find_session(tutor_sid)
    assert found is not None and found[0] == "tutor"
    found = find_session(dialogue_sid)
    assert found is not None and found[0] == "dialogue"
    assert find_session("does-not-exist") is None
    assert find_session("") is None


# ── 4. AgentMemory 短期记忆：跨池读取 ────────────────────────────────────────


def test_short_term_reads_from_any_pool():
    from app.services import tutor as tutor_service

    session = tutor_service._get_session(None, "nn")
    session["history"] = [
        {"role": "user", "content": "讲讲反向传播"},
        {"role": "assistant", "content": "先说链式法则…"},
    ]
    memory = AgentMemory()
    messages = memory.short_term(session["sessionId"])

    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "讲讲反向传播"
    assert all(isinstance(m, Message) for m in messages)


def test_short_term_is_safe_on_unknown_and_dirty():
    memory = AgentMemory()
    assert memory.short_term("") == []
    assert memory.short_term("s_nonexistent_session") == []

    from app.services import tutor as tutor_service

    session = tutor_service._get_session(None, "nn")
    session["history"] = [{"role": "user", "content": "ok"}, {"no_role": 1}, "not-a-dict"]
    got = memory.short_term(session["sessionId"])
    # 脏条目被跳过（读模型不该因脏数据崩溃），而非抛错
    assert [m.role for m in got] == ["user"]


def test_short_term_does_not_mutate_session():
    """只读语义：读取不续期、不创建、不改 history。"""
    from app.services import tutor as tutor_service

    session = tutor_service._get_session(None, "nn")
    sid = session["sessionId"]
    session["history"] = [{"role": "user", "content": "x"}]
    before_len = len(tutor_service._sessions)
    AgentMemory().short_term(sid)
    assert session["history"] == [{"role": "user", "content": "x"}]
    assert len(tutor_service._sessions) == before_len


# ── 5. 工作记忆：按用户隔离 ──────────────────────────────────────────────────


def test_working_memory_isolated_between_users():
    root = get_memory()
    alice = root.for_user("u_r41_alice")
    bob = root.for_user("u_r41_bob")
    try:
        alice.remember("goal", "我要搞懂 Transformer")
        assert alice.recall("goal") == "我要搞懂 Transformer"
        # 越权：同 key 不同用户读不到
        assert bob.recall("goal") is None
        # 忘记只影响本人
        assert alice.forget("goal") is True
        assert alice.recall("goal") is None
        assert bob.forget("goal") is False
    finally:
        alice.forget("goal")
        bob.forget("goal")


def test_working_memory_overwrites_same_key():
    root = get_memory()
    user = root.for_user("u_r41_ow")
    try:
        user.remember("k", "v1")
        user.remember("k", "v2")  # 覆盖而非追加
        assert user.recall("k") == "v2"
        assert user.recall("missing") is None
        with pytest.raises(ValueError, match="blank"):
            user.remember("   ", "v")
    finally:
        user.forget("k")


def test_working_memory_expires():
    root = get_memory()
    user = root.for_user("u_r41_ttl")
    user.remember("tmp", 1)
    # 手动把 TTL 戳过期（与 SessionStore 同一机制）
    user._working[f"u_r41_ttl:tmp"]["expiresAt"] = 0.0
    assert user.recall("tmp") is None


# ── 6. LongTermMemory：只读既有表、标准化 Evidence ───────────────────────────


def test_long_term_returns_normalized_evidence(client):
    reader = LongTermMemory()
    evidence = reader.query(_USER, "")
    kinds = {item.kind for item in evidence}
    assert {"mastery", "quiz"} <= kinds

    for item in evidence:
        assert item.user_id == _USER
        assert item.summary and 0.0 <= item.confidence <= 1.0
        # 可 JSON 序列化（State / checkpoint 的硬约束）
        json.dumps(item.model_dump(mode="json"), ensure_ascii=False, default=str)
        # 不得携带任何 ORM / Session 对象
        assert not any(
            hasattr(value, "_sa_instance_mapper") for value in item.value.values()
        )

    mastery = next(item for item in evidence if item.kind == "mastery")
    assert mastery.subject == "nn"
    assert "passed" in mastery.summary and "88" in mastery.summary
    assert mastery.value["score"] == 88
    assert mastery.confidence == 0.9  # 来自 mastery.confidence 列


def test_long_term_keyword_filter(db):
    reader = LongTermMemory()
    assert reader.query(_USER, "nn") != []          # subject 命中
    assert reader.query(_USER, "测验") != []         # summary 命中
    assert reader.query(_USER, "zzz_absent_term") == []
    assert reader.query(_USER, "") != []             # 空 query = 全量


def test_long_term_never_crosses_users(db):
    """空 user_id → 空；他人 user_id → 查不到我的数据（隔离键强制）。"""
    reader = LongTermMemory()
    assert reader.query("", "nn") == []
    assert reader.query("   ", "nn") == []
    assert reader.query("u_r41_stranger", "nn") == []


def test_long_term_limit_is_enforced(db):
    reader = LongTermMemory(limit=5)
    assert len(reader.query(_USER, "")) <= 5
    assert len(reader.query(_USER, "", limit=2)) <= 2
    with pytest.raises(ValueError):
        LongTermMemory(limit=0)


def test_evidence_kind_order_is_stable(db):
    """6 类证据按固定顺序返回（可复现，便于断言与 trace）。"""
    reader = LongTermMemory()
    kinds = [item.kind for item in reader.query(_USER, "")]
    order = list(reader.kinds())
    assert set(kinds) <= set(order)
    assert [order.index(k) for k in kinds] == sorted(order.index(k) for k in kinds)


def test_group_by_kind_and_as_dicts(db):
    reader = LongTermMemory()
    grouped = reader.group_by_kind(_USER, "")
    assert set(grouped) == set(reader.kinds())
    assert grouped["mastery"], "mastery 桶应有数据"

    from app.agents.memory import as_dicts

    dumped = as_dicts(grouped["mastery"])
    assert dumped and isinstance(dumped[0], dict)


# ── 7. Evidence / Message 自身契约 ───────────────────────────────────────────


def test_evidence_contract():
    item = Evidence(kind="quiz", user_id="u", subject="cnn", summary="测验 cnn：90分 通过")
    assert Evidence.model_validate(item.model_dump(mode="json")) == item

    with pytest.raises(Exception):
        Evidence(kind="quiz", user_id="u", summary="x", unexpected_field=1)
    with pytest.raises(Exception):
        Evidence(kind="quiz", user_id="u", summary="x", confidence=1.5)


def test_evidence_matches_terms():
    item = Evidence(
        kind="quiz", user_id="u", subject="cnn", summary="测验 cnn：90分 通过",
        value={"kpId": "cnn"},
    )
    assert item.matches(["cnn"])
    assert item.matches(["CNN"])          # 大小写不敏感
    assert item.matches(["测验"])          # 中文
    assert item.matches(["anything", "cnn"])  # 任一命中
    assert not item.matches(["transformer"])
    assert item.matches([])               # 空词组 → 全匹配


def test_split_terms_handles_punctuation():
    assert split_terms("  神经网络， CNN  ") == ["神经网络", "CNN"]
    assert split_terms("a;b、c；d") == ["a", "b", "c", "d"]
    assert split_terms("") == []
    assert split_terms(None) == []  # type: ignore[arg-type]


def test_message_allows_extra_keys_from_existing_history():
    """Message 是读模型：既有 history 带额外键也不该被拒。"""
    msg = Message.model_validate({"role": "user", "content": "hi", "extra": 1})
    assert msg.role == "user" and msg.content == "hi"
