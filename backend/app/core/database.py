"""SQLAlchemy 引擎 / 会话 / Base（B1）。

轻量栈：SQLite 嵌入式（CLAUDE.md 约定，不引入 PostgreSQL）。
- engine：单文件 SQLite，`check_same_thread=False` 适配 FastAPI 多线程依赖；
- SessionLocal：请求级会话工厂；
- Base：所有 ORM 模型的声明基类；
- get_db：FastAPI 依赖，保证会话开闭。

健壮性（演示 / 发布期防 `disk I/O error` 直出 500）：
- `pool_pre_ping`：取连接前先探活，自动剔除已失效的池内连接（如外部对 db 文件做
  热备份 / 轮转后失活的句柄），透明重连而非让请求 5000；
- 每条 SQLite 连接启用 **WAL**（读写并发、对在线读取 / 拷贝更鲁棒）+ `busy_timeout`
  （锁竞争时重试而非立即报错）+ `synchronous=NORMAL`（WAL 下安全且更快）。
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
# SQLite 需 check_same_thread=False 才能跨线程复用连接
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
    pool_pre_ping=True,  # 失效连接自动剔除并重连，避免坏句柄导致请求直接 5000
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        """每条新 SQLite 连接的健壮性 PRAGMA（WAL + 锁重试 + NORMAL 同步）。"""
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：产出一个请求级会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
