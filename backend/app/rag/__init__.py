"""RAG基础设施。

chunker（标题感知 + 重叠切片）→ embeddings（bge-small-zh，延迟加载/降级）
→ vector_store（Chroma 持久化）→ retriever（BM25 + 向量 RRF 融合）
→ reranker（bge-reranker，失败降级仅 RRF）。

所有重型本地模型（sentence-transformers / chromadb）均**延迟加载**且**加载失败自动降级**，
保证无网络、无模型文件时仍能跑通全链路（对齐 CLAUDE.md「无密钥可跑通」纪律）。

TASK-003-B2起，新RAG调用统一从``get_trusted_retrieval_pipeline``进入；旧Retriever
保留供兼容与渐进迁移使用。
"""

from app.rag.pipeline import (
    TrustedRetrievalPipeline,
    get_trusted_retrieval_pipeline,
)
from app.rag.protocol import EvidenceItem, QueryPlan, RAGRequest, RAGResponse

__all__ = [
    "EvidenceItem",
    "QueryPlan",
    "RAGRequest",
    "RAGResponse",
    "TrustedRetrievalPipeline",
    "get_trusted_retrieval_pipeline",
]
