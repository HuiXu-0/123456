from __future__ import annotations

from sqlalchemy import create_engine, inspect

from app.core.migrations import applied_versions, downgrade, upgrade


def test_migration_upgrade_downgrade_and_reupgrade(tmp_path):
    db_engine = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}", future=True)
    try:
        assert applied_versions(db_engine) == []

        assert upgrade(db_engine) == [1, 2]
        assert upgrade(db_engine) == []
        assert applied_versions(db_engine) == [1, 2]
        assert "async_tasks" in inspect(db_engine).get_table_names()
        assert "external_resource_cache" in inspect(db_engine).get_table_names()

        assert downgrade(db_engine, target=0) == [2, 1]
        assert downgrade(db_engine, target=0) == []
        assert applied_versions(db_engine) == []
        assert "async_tasks" not in inspect(db_engine).get_table_names()

        assert upgrade(db_engine) == [1, 2]
        assert applied_versions(db_engine) == [1, 2]
    finally:
        db_engine.dispose()


def test_migration_rejects_out_of_range_target(tmp_path):
    db_engine = create_engine(f"sqlite:///{(tmp_path / 'invalid.db').as_posix()}", future=True)
    try:
        try:
            upgrade(db_engine, target=99)
        except ValueError as exc:
            assert "目标版本越界" in str(exc)
        else:
            raise AssertionError("越界迁移目标必须被拒绝")
    finally:
        db_engine.dispose()
