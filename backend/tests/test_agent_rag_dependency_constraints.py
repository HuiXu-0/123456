"""Static dependency guard for Agent-side RAG access (TASK-003-E3)."""
from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_MODULE_PARTS = {
    "retriever",
    "vector_store",
    "embeddings",
    "embedding",
    "reranker",
    "chroma",
    "chromadb",
}
FORBIDDEN_IMPORTED_NAMES = {
    "Retriever",
    "VectorStore",
    "Embedding",
    "Embedder",
    "Reranker",
    "Chroma",
}


def test_agent_modules_do_not_import_rag_infrastructure_directly():
    agents_dir = Path(__file__).resolve().parents[1] / "app" / "agents"
    violations: list[str] = []

    for path in sorted(agents_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = set(alias.name.lower().split("."))
                    if parts & FORBIDDEN_MODULE_PARTS:
                        violations.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module_parts = set((node.module or "").lower().split("."))
                names = {alias.name for alias in node.names}
                if module_parts & FORBIDDEN_MODULE_PARTS or names & FORBIDDEN_IMPORTED_NAMES:
                    violations.append(
                        f"{path.name}:{node.lineno} from {node.module} import {sorted(names)}"
                    )

    assert violations == []
