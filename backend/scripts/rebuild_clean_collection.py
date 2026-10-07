"""从UTF-8 seed_docs蓝绿重建干净Chroma Collection（TASK-003-C2）。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.rag.chunker import DocumentChunker
from app.rag.embeddings import Embedder, EmbeddingProfile
from app.rag.text_quality import (
    find_duplicate_chunks,
    inspect_text_quality,
    read_utf8_strict,
    validate_text_quality,
)

CHUNKING_VERSION = "heading-window-v1"


def _profile(provider: str) -> EmbeddingProfile:
    if provider == "hash":
        return EmbeddingProfile("hash", "deterministic-hash-v1", settings.embedding_dimension)
    return EmbeddingProfile(
        "sentence-transformers",
        settings.embedding_model_name,
        settings.embedding_dimension,
    )


def prepare_seed_chunks(seed_dir: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(seed_dir)
    chunker = DocumentChunker(settings.chunk_size, settings.chunk_overlap)
    chunks: list[dict[str, Any]] = []
    bad_documents: list[dict[str, Any]] = []
    source_paths = sorted(root.rglob("*.md"))
    document_ids: set[str] = set()
    for path in source_paths:
        text = read_utf8_strict(path)
        quality = inspect_text_quality(text)
        if not quality.valid:
            bad_documents.append(
                {"file": path.relative_to(root).as_posix(), "issues": quality.issues}
            )
            continue
        prefix = path.name.split("-", 1)[0]
        relative_parent = path.parent.relative_to(root)
        if relative_parent == Path(".") and prefix.isdigit():
            document_id = f"doc_{int(prefix):03d}"
        else:
            parts = path.stem.split("-", 2)
            if len(parts) < 2 or not parts[1].isdigit():
                raise ValueError(
                    "子目录seed文档须使用<领域>-<数字>-<标题>命名："
                    f"{path.relative_to(root).as_posix()}"
                )
            domain = re.sub(r"[^a-z0-9]+", "_", parts[0].lower()).strip("_")
            if not domain:
                raise ValueError(f"seed文档领域标识无效：{path.name}")
            document_id = f"doc_{domain}_{int(parts[1]):03d}"
        if document_id in document_ids:
            raise ValueError(f"seed文档ID冲突：{document_id}")
        document_ids.add(document_id)
        document_title = path.stem.split("-", 1)[-1]
        document_chunks = chunker.chunk_document(
            text,
            document_id=document_id,
            document_title=document_title,
        )
        for chunk in document_chunks:
            chunk["id"] = f"{document_id}#{chunk['chunk_index']}"
            validate_text_quality(chunk["content"], context=f"{chunk['id']} ")
            chunk["metadata"].update(
                {
                    "source_encoding": "utf-8",
                    "chunking_version": CHUNKING_VERSION,
                }
            )
        chunks.extend(document_chunks)
    duplicates = find_duplicate_chunks(chunks)
    report = {
        "documentCount": len(source_paths),
        "chunkCount": len(chunks),
        "badDocuments": bad_documents,
        "badChunks": [],
        "emptyChunks": [chunk["id"] for chunk in chunks if not chunk["content"].strip()],
        "duplicateChunkGroups": duplicates,
    }
    return chunks, report


def audit_collection(client: Any, name: str) -> dict[str, Any]:
    collection = client.get_collection(name=name)
    payload = collection.get(include=["documents", "metadatas"])
    bad_chunks = []
    empty_chunks = []
    rows = []
    for chunk_id, content, metadata in zip(
        payload.get("ids") or [],
        payload.get("documents") or [],
        payload.get("metadatas") or [],
    ):
        quality = inspect_text_quality(content or "")
        if not quality.valid:
            bad_chunks.append({"chunkId": chunk_id, "issues": quality.issues})
        if not (content or "").strip():
            empty_chunks.append(chunk_id)
        rows.append({"id": chunk_id, "content": content or "", "metadata": metadata or {}})
    sample = collection.get(limit=1, include=["embeddings"])
    embeddings = sample.get("embeddings")
    dimension = len(embeddings[0]) if embeddings is not None and len(embeddings) else None
    document_ids = {row["metadata"].get("document_id") for row in rows}
    return {
        "name": name,
        "metadata": collection.metadata or {},
        "documentCount": len(document_ids - {None}),
        "chunkCount": len(rows),
        "embeddingDimension": dimension,
        "badChunkCount": len(bad_chunks),
        "badChunks": bad_chunks,
        "emptyChunks": empty_chunks,
        "duplicateChunkGroups": find_duplicate_chunks(rows),
    }


def rebuild_collection(
    *,
    seed_dir: str | Path,
    target: str,
    provider: str,
    dry_run: bool = True,
    client: Any | None = None,
    audit_existing: str | None = None,
) -> dict[str, Any]:
    if provider not in {"hash", "real"}:
        raise ValueError("provider必须为hash或real")
    chunks, source_report = prepare_seed_chunks(seed_dir)
    profile = _profile(provider)
    result: dict[str, Any] = {
        "dryRun": dry_run,
        "target": target,
        "embeddingModeRequested": "hash_fallback" if provider == "hash" else "real_embedding",
        "embeddingProfileId": profile.profile_id,
        "sourceEncoding": "utf-8",
        "chunkingVersion": CHUNKING_VERSION,
        "source": source_report,
    }
    if audit_existing:
        if client is None:
            import chromadb

            client = chromadb.PersistentClient(path=settings.chroma_dir)
        result["existingCollectionAudit"] = audit_collection(client, audit_existing)
    if dry_run:
        return result
    if source_report["badDocuments"] or source_report["badChunks"]:
        raise ValueError("源数据质量检查失败，拒绝重建Collection")
    if client is None:
        import chromadb

        client = chromadb.PersistentClient(path=settings.chroma_dir)
    try:
        existing = client.get_collection(name=target)
    except Exception:  # Chroma不存在集合时抛NotFoundError，各版本类型不同
        existing = None
    if existing is not None:
        raise ValueError(f"目标Collection已存在，拒绝覆盖：{target}")

    embedder = Embedder(profile=profile, allow_fallback=False if provider == "real" else True)
    status = embedder.require_real() if provider == "real" else embedder.status(load=True)
    created_at = datetime.now(timezone.utc).isoformat()
    collection = client.create_collection(
        name=target,
        metadata={
            "hnsw:space": "cosine",
            "embedding_profile_id": profile.profile_id,
            "embedding_provider": profile.provider,
            "embedding_model": profile.model_name,
            "embedding_dimension": profile.dimension,
            "source_encoding": "utf-8",
            "chunking_version": CHUNKING_VERSION,
            "created_at": created_at,
        },
    )
    batch_size = 64
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        contents = [chunk["content"] for chunk in batch]
        collection.add(
            ids=[chunk["id"] for chunk in batch],
            documents=contents,
            metadatas=[chunk["metadata"] for chunk in batch],
            embeddings=embedder.embed_texts(contents),
        )
    result["embeddingRuntime"] = status
    result["validation"] = audit_collection(client, target)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="蓝绿重建UTF-8干净知识库Collection")
    parser.add_argument("--seed-dir", default=str(_BACKEND_ROOT / "seed_docs"))
    parser.add_argument("--target", required=True)
    parser.add_argument("--provider", choices=["hash", "real"], required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--audit-existing", help="同时只读审计旧Collection乱码与质量")
    args = parser.parse_args()
    report = rebuild_collection(
        seed_dir=args.seed_dir,
        target=args.target,
        provider=args.provider,
        dry_run=not args.execute,
        audit_existing=args.audit_existing,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
