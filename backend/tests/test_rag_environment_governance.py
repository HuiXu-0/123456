"""TASK-003-C2编码、Embedding模式、Collection重建和稳态统计测试。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from app.rag.embeddings import (
    Embedder,
    EmbeddingProfile,
    EmbeddingUnavailableError,
)
from app.rag.evaluation import measure_latency_phases
from app.rag.text_quality import (
    TextEncodingError,
    TextQualityError,
    find_duplicate_chunks,
    inspect_text_quality,
    read_utf8_strict,
    validate_text_quality,
)
from scripts.rebuild_clean_collection import rebuild_collection


def test_utf8_reader_preserves_chinese(tmp_path):
    path = tmp_path / "valid.md"
    path.write_bytes("# 标题\n神经网络与反向传播。".encode("utf-8"))
    assert read_utf8_strict(path) == "# 标题\n神经网络与反向传播。"


def test_non_utf8_reader_fails_explicitly(tmp_path):
    path = tmp_path / "gbk.md"
    path.write_bytes("中文内容".encode("gbk"))
    with pytest.raises(TextEncodingError, match="非UTF-8文件"):
        read_utf8_strict(path)


@pytest.mark.parametrize(
    ("text", "issue"),
    [
        ("正常文字\ufffd损坏", "replacement_character"),
        ("正常文字\x00控制", "unexpected_control_character"),
        ("Transformer ÊÇ µÄ £¬乱码", "mojibake_signature"),
    ],
)
def test_mojibake_detection(text, issue):
    report = inspect_text_quality(text)
    assert report.valid is False
    assert issue in report.issues
    with pytest.raises(TextQualityError):
        validate_text_quality(text, context="test")


def test_high_non_printable_ratio_is_detected():
    report = inspect_text_quality("正常文本" + "\x00" * 3)
    assert "high_non_printable_ratio" in report.issues


def test_empty_and_duplicate_chunks_are_detected():
    assert "empty_text" in inspect_text_quality("   ").issues
    duplicates = find_duplicate_chunks(
        [
            {"id": "a", "content": "相同内容"},
            {"id": "b", "content": "相同内容"},
            {"id": "c", "content": "不同内容"},
        ]
    )
    assert duplicates == [["a", "b"]]


def test_embedding_runtime_hash_mode_is_explicit():
    embedder = Embedder(
        profile=EmbeddingProfile("hash", "deterministic-hash-v1", 8)
    )
    assert embedder.status()["mode"] == "hash_fallback"
    assert len(embedder.embed_query("测试")) == 8


def test_force_real_failure_is_not_silently_downgraded(monkeypatch):
    fake_module = types.ModuleType("sentence_transformers")

    class FailingModel:
        def __init__(self, *args, **kwargs):
            raise PermissionError("torch dll denied")

    fake_module.SentenceTransformer = FailingModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    embedder = Embedder(
        profile=EmbeddingProfile("sentence-transformers", "BAAI/bge-small-zh-v1.5", 512),
        allow_fallback=False,
    )
    with pytest.raises(EmbeddingUnavailableError, match="禁止fallback"):
        embedder.require_real()
    with pytest.raises(EmbeddingUnavailableError):
        embedder.embed_query("不得在第二次调用时静默降级")


def test_collection_rebuild_dry_run_reads_all_seed_documents():
    seed_dir = Path(__file__).resolve().parents[1] / "seed_docs"
    report = rebuild_collection(
        seed_dir=seed_dir,
        target="dry_run_collection",
        provider="hash",
        dry_run=True,
    )
    assert report["dryRun"] is True
    assert report["source"]["documentCount"] == 35
    assert report["source"]["chunkCount"] >= 150
    assert report["source"]["badDocuments"] == []
    assert report["source"]["emptyChunks"] == []
    assert report["source"]["duplicateChunkGroups"] == []


def test_rebuilt_collection_metadata_and_validation(tmp_path):
    chromadb = pytest.importorskip("chromadb")
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    seed_dir = Path(__file__).resolve().parents[1] / "seed_docs"
    report = rebuild_collection(
        seed_dir=seed_dir,
        target="kb_chunks_hash_d512_test_v1",
        provider="hash",
        dry_run=False,
        client=client,
    )
    validation = report["validation"]
    assert validation["documentCount"] == 35
    assert validation["chunkCount"] == report["source"]["chunkCount"]
    assert validation["embeddingDimension"] == 512
    assert validation["badChunkCount"] == 0
    assert validation["emptyChunks"] == []
    assert validation["duplicateChunkGroups"] == []
    metadata = validation["metadata"]
    assert metadata["embedding_profile_id"].startswith("hash:")
    assert metadata["embedding_provider"] == "hash"
    assert metadata["embedding_dimension"] == 512
    assert metadata["source_encoding"] == "utf-8"
    assert metadata["chunking_version"]
    assert metadata["created_at"]


def test_cold_warm_steady_stats_and_alternating_order():
    calls = []

    def old():
        calls.append("old")

    def new():
        calls.append("new")

    report = measure_latency_phases(old, new, warmup_rounds=2, steady_rounds=4)
    assert report["coldStart"].keys() == {"oldMs", "newMs"}
    assert report["steadyState"]["old"]["sampleCount"] == 4
    assert report["steadyState"]["new"]["sampleCount"] == 4
    assert report["executionOrder"] == [
        ["old", "new"], ["new", "old"], ["old", "new"], ["new", "old"]
    ]
    assert calls[:2] == ["old", "new"]
