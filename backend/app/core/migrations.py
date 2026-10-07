"""SQLite 轻量迁移账本。

不引入 Alembic 等额外运行时依赖；每个迁移同时声明 upgrade/downgrade SQL，
并由 schema_migrations 记录已应用版本。迁移必须可重复执行。
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Engine, text

from app.core.database import engine as default_engine


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    upgrade_sql: tuple[str, ...]
    downgrade_sql: tuple[str, ...]


MIGRATIONS = (
    Migration(
        version=1,
        name="persistent_async_tasks",
        upgrade_sql=(
            """
            CREATE TABLE async_tasks (
                task_id VARCHAR(64) PRIMARY KEY,
                status VARCHAR(16) NOT NULL,
                progress INTEGER,
                result_json TEXT,
                error_json TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """,
            "CREATE INDEX ix_async_tasks_status ON async_tasks(status)",
        ),
        downgrade_sql=("DROP TABLE async_tasks",),
    ),
    Migration(
        version=2,
        name="external_resource_search_cache",
        upgrade_sql=(
            """
            CREATE TABLE external_resource_cache (
                cache_key VARCHAR(64) PRIMARY KEY,
                kp_id VARCHAR(32) NOT NULL,
                provider VARCHAR(32) NOT NULL,
                query VARCHAR(512) NOT NULL,
                items_json TEXT NOT NULL,
                fetched_at DATETIME NOT NULL,
                expires_at DATETIME NOT NULL
            )
            """,
            "CREATE INDEX ix_external_resource_cache_kp_id ON external_resource_cache(kp_id)",
        ),
        downgrade_sql=("DROP TABLE external_resource_cache",),
    ),
    Migration(
        version=3,
        name="async_tasks_user_and_type",
        upgrade_sql=(
            "ALTER TABLE async_tasks ADD COLUMN user_id VARCHAR(64)",
            "CREATE INDEX ix_async_tasks_user_id ON async_tasks(user_id)",
            "ALTER TABLE async_tasks ADD COLUMN task_type VARCHAR(32)",
            "CREATE INDEX ix_async_tasks_task_type ON async_tasks(task_type)",
            "ALTER TABLE async_tasks ADD COLUMN scope_key VARCHAR(256)",
        ),
        downgrade_sql=(
            "ALTER TABLE async_tasks DROP COLUMN user_id",
            "ALTER TABLE async_tasks DROP COLUMN task_type",
            "ALTER TABLE async_tasks DROP COLUMN scope_key",
        ),
    ),
)


def _ensure_ledger(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, name VARCHAR(128) NOT NULL, "
                "applied_at DATETIME NOT NULL)"
            )
        )


def applied_versions(db_engine: Engine = default_engine) -> list[int]:
    """返回已应用版本（升序）；首次调用会初始化迁移账本。"""
    _ensure_ledger(db_engine)
    with db_engine.connect() as conn:
        return list(conn.execute(text("SELECT version FROM schema_migrations ORDER BY version")).scalars())


def upgrade(db_engine: Engine = default_engine, target: int | None = None) -> list[int]:
    """升级到 target（默认最新），返回本次实际应用的版本。"""
    _ensure_ledger(db_engine)
    latest = MIGRATIONS[-1].version if MIGRATIONS else 0
    wanted = latest if target is None else target
    if wanted < 0 or wanted > latest:
        raise ValueError(f"迁移目标版本越界: {wanted}（最新 {latest}）")
    applied = set(applied_versions(db_engine))
    changed: list[int] = []
    for migration in MIGRATIONS:
        if migration.version > wanted or migration.version in applied:
            continue
        with db_engine.begin() as conn:
            for statement in migration.upgrade_sql:
                conn.execute(text(statement))
            conn.execute(
                text(
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    "VALUES (:version, :name, :applied_at)"
                ),
                {
                    "version": migration.version,
                    "name": migration.name,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        changed.append(migration.version)
    return changed


def downgrade(db_engine: Engine = default_engine, target: int = 0) -> list[int]:
    """回滚到 target，返回本次实际回滚的版本（降序）。"""
    _ensure_ledger(db_engine)
    latest = MIGRATIONS[-1].version if MIGRATIONS else 0
    if target < 0 or target > latest:
        raise ValueError(f"迁移目标版本越界: {target}（最新 {latest}）")
    migration_by_version = {item.version: item for item in MIGRATIONS}
    changed: list[int] = []
    for version in sorted(applied_versions(db_engine), reverse=True):
        if version <= target:
            continue
        migration = migration_by_version.get(version)
        if migration is None:
            raise RuntimeError(f"数据库含未知迁移版本: {version}")
        with db_engine.begin() as conn:
            for statement in migration.downgrade_sql:
                conn.execute(text(statement))
            conn.execute(
                text("DELETE FROM schema_migrations WHERE version = :version"),
                {"version": version},
            )
        changed.append(version)
    return changed


def _main() -> None:
    parser = argparse.ArgumentParser(description="智学中枢 SQLite 轻量迁移")
    parser.add_argument("action", choices=("upgrade", "downgrade", "current"))
    parser.add_argument("--target", type=int)
    args = parser.parse_args()
    if args.action == "upgrade":
        print({"applied": upgrade(target=args.target)})
    elif args.action == "downgrade":
        print({"rolledBack": downgrade(target=0 if args.target is None else args.target)})
    else:
        print({"versions": applied_versions()})


if __name__ == "__main__":
    _main()
