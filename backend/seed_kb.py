"""知识库一键灌库脚本（B3）。

将 backend/seed_docs/ 下的 markdown 讲义同步灌入知识库（建文档记录 → 切片 → 向量化
→ 写入 Chroma），供管理端「检索测试」直接验证召回。**幂等**：同名 filename 已存在则跳过。

与接口 14.1 的异步入库共用同一套 RAG 组件（chunker/embedder/vector_store），此处为脚本
方便而同步执行。

运行：
    cd backend
    python seed_kb.py
"""
from __future__ import annotations

import os

from app.core.database import SessionLocal
from app.core.init_db import init_db
from app.core.tasks import Task
from app.models.entities import KnowledgeDocument
from app.services import kb as kb_service

SEED_DIR = os.path.join(os.path.dirname(__file__), "seed_docs")


def main() -> None:
    init_db()  # 确保建表
    db = SessionLocal()
    doc_specs: list[tuple[str, str, bytes]] = []
    try:
        files = sorted(f for f in os.listdir(SEED_DIR) if f.lower().endswith((".md", ".txt")))
        for filename in files:
            # 幂等：同名 filename 已灌过则跳过
            exists = (
                db.query(KnowledgeDocument)
                .filter(KnowledgeDocument.filename == filename)
                .first()
            )
            if exists is not None:
                print(f"[seed_kb] 跳过已存在文档：{filename}（{exists.id}）")
                continue
            with open(os.path.join(SEED_DIR, filename), "rb") as fp:
                raw = fp.read()
            doc_id = kb_service._next_doc_id(db)
            db.add(
                KnowledgeDocument(
                    id=doc_id,
                    title=kb_service._derive_title(filename),
                    filename=filename,
                    size=len(raw),
                    category="教材",
                    status="pending",
                    chunks=0,
                )
            )
            db.flush()
            doc_specs.append((doc_id, filename, raw))
            print(f"[seed_kb] 登记文档：{filename} → {doc_id}")
        db.commit()
    finally:
        db.close()

    if not doc_specs:
        print("[seed_kb] 无新增文档，知识库已是最新。")
        return

    result = kb_service._do_ingest(doc_specs, Task(task_id="seed"))
    count = kb_service.get_vector_store().count()
    print(f"[seed_kb] 灌库完成：{result}，向量库当前 chunk 总数={count}")


if __name__ == "__main__":
    main()
