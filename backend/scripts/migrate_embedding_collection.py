"""Chroma Collection重新Embedding迁移工具。

默认dry-run，只读取源Collection并输出迁移计划。传入--execute后才创建目标Collection；
不删除、不修改源Collection，也不覆盖非空目标Collection。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# 支持从backend目录直接执行：python scripts/migrate_embedding_collection.py ...
_BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.core.config import settings
from app.rag.embeddings import Embedder, get_embedding_profile


def migrate_collection(
    source: str,
    target: str,
    *,
    dry_run: bool = True,
    batch_size: int = 64,
    client: Any | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    if source == target:
        raise ValueError("源Collection与目标Collection不能相同")
    if batch_size <= 0:
        raise ValueError("batch_size必须大于0")
    if client is None:
        import chromadb

        client = chromadb.PersistentClient(path=settings.chroma_dir)

    source_collection = client.get_collection(name=source)
    source_count = source_collection.count()
    source_dimension = None
    source_metadata = source_collection.metadata or {}
    if source_metadata.get("embedding_dimension") is not None:
        source_dimension = int(source_metadata["embedding_dimension"])
    elif source_count:
        sample = source_collection.get(limit=1, include=["embeddings"])
        embeddings = sample.get("embeddings")
        if embeddings is not None and len(embeddings):
            source_dimension = len(embeddings[0])
    profile = (embedder.profile if embedder is not None else get_embedding_profile())
    plan = {
        "source": source,
        "target": target,
        "documents": source_count,
        "sourceDimension": source_dimension,
        "embeddingProfile": profile.profile_id,
        "dimension": profile.dimension,
        "dryRun": dry_run,
    }
    if dry_run:
        return plan

    target_collection = client.get_or_create_collection(
        name=target,
        metadata={
            "hnsw:space": "cosine",
            "embedding_provider": profile.provider,
            "embedding_model": profile.model_name,
            "embedding_dimension": profile.dimension,
            "embedding_profile_id": profile.profile_id,
            "migrated_from": source,
        },
    )
    if target_collection.count():
        raise ValueError(f"目标Collection非空，拒绝覆盖：{target}")

    encoder = embedder or Embedder()
    for offset in range(0, source_count, batch_size):
        page = source_collection.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        documents = page.get("documents") or []
        if not documents:
            continue
        target_collection.add(
            ids=page["ids"],
            documents=documents,
            metadatas=page.get("metadatas") or [{} for _ in documents],
            embeddings=encoder.embed_texts(documents),
        )
    plan["migrated"] = target_collection.count()
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="重新Embedding迁移Chroma Collection")
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--execute", action="store_true", help="实际执行；默认仅dry-run")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    result = migrate_collection(
        args.source,
        args.target,
        dry_run=not args.execute,
        batch_size=args.batch_size,
    )
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
