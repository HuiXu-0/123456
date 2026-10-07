"""TASK-003-B1 Embedding配置、维度治理和迁移dry-run测试。"""
from __future__ import annotations

import threading

import pytest

from app.rag.embeddings import (
    EmbeddingDimensionError,
    EmbeddingProfile,
    get_embedding_profile,
)
from app.rag.vector_store import _ChromaStore, _NumpyStore
from app.rag import vector_store as vector_store_module
from scripts.migrate_embedding_collection import migrate_collection


def test_embedding_configuration_is_centralized():
    profile = get_embedding_profile()
    assert profile.provider
    assert profile.model_name
    assert profile.dimension > 0
    assert profile.profile_id.endswith(f":d{profile.dimension}")


def test_collection_add_and_query_validate_dimension():
    profile = EmbeddingProfile("test", "unit-model", 3)
    store = object.__new__(_NumpyStore)
    store.profile = profile
    store._path = "unused.json"
    store._items = {}
    store._lock = threading.Lock()
    with pytest.raises(EmbeddingDimensionError, match="expected=3, actual=2"):
        store.add(["c1"], [[0.1, 0.2]], ["text"], [{}])
    with pytest.raises(EmbeddingDimensionError, match="expected=3, actual=4"):
        store.query([0.1, 0.2, 0.3, 0.4], 1)


class _DeclaredCollection:
    metadata = {"embedding_dimension": 256}

    def count(self):
        return 0


def test_existing_collection_profile_dimension_is_validated():
    store = object.__new__(_ChromaStore)
    store._name = "legacy_collection"
    store.profile = EmbeddingProfile("test", "unit-model", 512)
    store._col = _DeclaredCollection()
    with pytest.raises(EmbeddingDimensionError, match="expected=512, actual=256"):
        store._validate_collection_profile()


def test_dimension_error_is_not_hidden_by_store_fallback(monkeypatch):
    def incompatible(*args, **kwargs):
        raise EmbeddingDimensionError("Collection expected=512, actual=256")

    monkeypatch.setattr(vector_store_module, "_ChromaStore", incompatible)
    with pytest.raises(EmbeddingDimensionError, match="expected=512, actual=256"):
        vector_store_module.get_collection_store("legacy")


class _SourceCollection:
    metadata = {"embedding_dimension": 256}

    def count(self):
        return 7


class _DryRunClient:
    def __init__(self):
        self.created = False

    def get_collection(self, name):
        assert name == "legacy"
        return _SourceCollection()

    def get_or_create_collection(self, **kwargs):
        self.created = True
        raise AssertionError("dry-run不得创建目标Collection")


def test_collection_migration_dry_run_has_no_writes():
    client = _DryRunClient()
    result = migrate_collection("legacy", "target", dry_run=True, client=client)
    assert result["source"] == "legacy"
    assert result["target"] == "target"
    assert result["documents"] == 7
    assert result["sourceDimension"] == 256
    assert result["dryRun"] is True
    assert client.created is False
