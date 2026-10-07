"""向量库（B3，Chroma 持久化集合 + 降级内存库）。

优先使用 Chroma 持久化集合（`PersistentClient(path=chroma_dir)`）；当 chromadb 未安装
或初始化失败时，降级为进程内 numpy 余弦库（仍按 chroma_dir 持久化为 JSON），打 WARNING，
保证全链路可跑（CLAUDE.md「轻量优先 / 无密钥可跑通」纪律）。

两种实现暴露同一接口：add / query / delete_by_document / get_all / count。
距离统一转为「相似度分数」（0-1，越大越相关），便于上层 RRF 与展示。
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading

from app.core.config import settings
from app.rag.embeddings import (
    EmbeddingDimensionError,
    EmbeddingProfile,
    get_embedding_profile,
    validate_vector_dimension,
)
from app.rag.text_quality import validate_text_quality

logger = logging.getLogger("app.rag.vector_store")

_COLLECTION = "kb_chunks"


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _safe_collection(name: str) -> str:
    """把任意集合名收敛为 Chroma 合法名（3-63 位、[a-zA-Z0-9._-]、首尾字母数字）。"""
    import re

    n = re.sub(r"[^a-zA-Z0-9._-]", "_", name or "")[:63]
    if len(n) < 3:
        n = (n + "___")[:3]
    return n


class _NumpyStore:
    """降级内存向量库：JSON 持久化 + 余弦检索。

    collection 命名空间隔离：每个集合独立 JSON 文件（fallback_store[__<collection>].json），
    保证「文档专属集合」与内置 kb_chunks 互不污染（Chroma 不可用时的降级路径同样隔离）。
    """

    def __init__(
        self,
        path: str,
        collection: str = _COLLECTION,
        profile: EmbeddingProfile | None = None,
    ) -> None:
        self.profile = profile or get_embedding_profile()
        suffix = "" if collection == _COLLECTION else f"__{_safe_collection(collection)}"
        self._path = os.path.join(path, f"fallback_store{suffix}.json")
        os.makedirs(path, exist_ok=True)
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()
        for cid, item in self._items.items():
            validate_vector_dimension(
                item.get("embedding") or [],
                expected=self.profile.dimension,
                context=f"Collection {self._path} stored[{cid}] ",
            )

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    self._items = json.load(f)
            except (OSError, ValueError):
                self._items = {}

    def _persist(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False)

    def add(self, ids, embeddings, documents, metadatas) -> None:
        for index, document in enumerate(documents):
            validate_text_quality(str(document), context=f"Collection写入[{index}] ")
        for index, vector in enumerate(embeddings):
            validate_vector_dimension(
                vector,
                expected=self.profile.dimension,
                context=f"Collection {collection_label(self)} add[{index}] ",
            )
        with self._lock:
            for i, cid in enumerate(ids):
                self._items[cid] = {
                    "embedding": embeddings[i],
                    "document": documents[i],
                    "metadata": metadatas[i],
                }
            self._persist()

    def query(self, embedding, n_results):
        validate_vector_dimension(
            embedding,
            expected=self.profile.dimension,
            context=f"Collection {collection_label(self)} query ",
        )
        scored = [
            (cid, _cosine(embedding, it["embedding"]), it)
            for cid, it in self._items.items()
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        out = []
        for cid, sim, it in scored[:n_results]:
            out.append(
                {
                    "id": cid,
                    "content": it["document"],
                    "metadata": it["metadata"],
                    "score": max(0.0, min(1.0, sim)),
                }
            )
        return out

    def delete_by_document(self, document_id: str) -> int:
        with self._lock:
            victims = [
                cid
                for cid, it in self._items.items()
                if it["metadata"].get("document_id") == document_id
            ]
            for cid in victims:
                del self._items[cid]
            self._persist()
            return len(victims)

    def get_all(self):
        return [
            {"id": cid, "content": it["document"], "metadata": it["metadata"]}
            for cid, it in self._items.items()
        ]

    def count(self) -> int:
        return len(self._items)


class _ChromaStore:
    """Chroma 持久化集合封装。"""

    def __init__(
        self,
        path: str,
        collection: str = _COLLECTION,
        profile: EmbeddingProfile | None = None,
    ) -> None:
        import chromadb

        os.makedirs(path, exist_ok=True)
        self._name = _safe_collection(collection)
        self.profile = profile or get_embedding_profile()
        self._client = chromadb.PersistentClient(path=path)
        # 余弦空间；嵌入由本层显式提供（不使用 chroma 内置 embedding function）。
        # 文档学习传入专属 collection 名 → 与内置 kb_chunks 物理隔离、互不污染。
        self._col = self._client.get_or_create_collection(
            name=self._name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_provider": self.profile.provider,
                "embedding_model": self.profile.model_name,
                "embedding_dimension": self.profile.dimension,
                "embedding_profile_id": self.profile.profile_id,
            },
        )
        self._validate_collection_profile()

    def _validate_collection_profile(self) -> None:
        metadata = self._col.metadata or {}
        declared = metadata.get("embedding_dimension")
        if declared is not None and int(declared) != self.profile.dimension:
            raise EmbeddingDimensionError(
                f"Collection {self._name}维度不一致："
                f"expected={self.profile.dimension}, actual={declared}"
            )
        if self._col.count() == 0:
            return
        sample = self._col.get(limit=1, include=["embeddings"])
        embeddings = sample.get("embeddings")
        if embeddings is not None and len(embeddings):
            validate_vector_dimension(
                list(embeddings[0]),
                expected=self.profile.dimension,
                context=f"Collection {self._name} stored ",
            )

    def add(self, ids, embeddings, documents, metadatas) -> None:
        for index, document in enumerate(documents):
            validate_text_quality(str(document), context=f"Collection {self._name}写入[{index}] ")
        for index, vector in enumerate(embeddings):
            validate_vector_dimension(
                vector,
                expected=self.profile.dimension,
                context=f"Collection {self._name} add[{index}] ",
            )
        self._col.add(
            ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas
        )

    def query(self, embedding, n_results):
        validate_vector_dimension(
            embedding,
            expected=self.profile.dimension,
            context=f"Collection {self._name} query ",
        )
        n = min(n_results, max(1, self._col.count()))
        res = self._col.query(
            query_embeddings=[embedding],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        out = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for i, cid in enumerate(ids):
            # cosine distance ∈ [0,2] → 相似度 1 - d，裁剪到 [0,1]
            sim = 1.0 - float(dists[i])
            out.append(
                {
                    "id": cid,
                    "content": docs[i],
                    "metadata": metas[i],
                    "score": max(0.0, min(1.0, sim)),
                }
            )
        return out

    def delete_by_document(self, document_id: str) -> int:
        existing = self._col.get(where={"document_id": document_id})
        ids = existing.get("ids", [])
        if ids:
            self._col.delete(ids=ids)
        return len(ids)

    def get_all(self):
        got = self._col.get(include=["documents", "metadatas"])
        ids = got.get("ids", [])
        docs = got.get("documents", [])
        metas = got.get("metadatas", [])
        return [
            {"id": ids[i], "content": docs[i], "metadata": metas[i]}
            for i in range(len(ids))
        ]

    def count(self) -> int:
        return self._col.count()


_store = None
_store_lock = threading.Lock()


def collection_label(store: object) -> str:
    return str(getattr(store, "_name", getattr(store, "_path", "unknown")))


def get_vector_store():
    """返回向量库单例：优先 Chroma，失败降级 numpy 库（内置知识库 kb_chunks 集合）。"""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        path = settings.chroma_dir
        try:
            _store = _ChromaStore(path)
            logger.info("向量库：Chroma 持久化集合就绪（path=%s）", path)
        except EmbeddingDimensionError:
            raise
        except Exception as exc:  # noqa: BLE001 chromadb 不可用 → 降级
            logger.warning(
                "Chroma 初始化失败，降级为内存余弦向量库（JSON 持久化）：%s", exc
            )
            _store = _NumpyStore(path)
        return _store


# ---- 命名集合工厂（「文档学习」专属向量集合隔离，与内置 kb_chunks 分开） ----------
# 每次返回一个绑定到指定 collection 的**新实例**（非单例）——文档专属集合读写量小、
# 按需实例化即可；与内置库共用 chroma_dir 但集合名不同，物理隔离、互不污染。
def get_collection_store(collection: str):
    """返回绑定到指定 collection 的向量库（优先 Chroma，失败降级 numpy 命名空间库）。"""
    path = settings.chroma_dir
    try:
        return _ChromaStore(path, collection=collection)
    except EmbeddingDimensionError:
        raise
    except Exception as exc:  # noqa: BLE001 chromadb 不可用 → 降级（同样按集合隔离）
        logger.warning(
            "Chroma 命名集合 %s 初始化失败，降级为内存余弦向量库：%s", collection, exc
        )
        return _NumpyStore(path, collection=collection)


def drop_collection(collection: str) -> None:
    """删除指定 collection 的全部向量（文档删除时清理其专属集合）。降级库删对应 JSON。"""
    path = settings.chroma_dir
    try:
        import chromadb

        client = chromadb.PersistentClient(path=path)
        client.delete_collection(name=_safe_collection(collection))
        return
    except Exception as exc:  # noqa: BLE001 Chroma 不可用或集合不存在 → 尝试降级库清理
        logger.info("drop_collection(%s) Chroma 路径跳过（%s），尝试降级库清理", collection, exc)
    suffix = f"__{_safe_collection(collection)}"
    fpath = os.path.join(path, f"fallback_store{suffix}.json")
    try:
        if os.path.exists(fpath):
            os.remove(fpath)
    except OSError:
        pass
