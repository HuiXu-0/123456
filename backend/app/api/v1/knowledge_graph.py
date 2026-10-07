"""知识图谱模块 Knowledge Graph（接口文档第 10 章，B6）。

接口 26：
- GET /knowledge-graph  12 节点 / 14 边 + category 按 mastery 实时推导

需登录（category/value 依赖当前用户掌握度）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import success
from app.core.security import get_current_user
from app.models.entities import User
from app.services import knowledge_graph as kg_service

router = APIRouter(tags=["knowledge-graph"])


@router.get("/knowledge-graph")
async def get_knowledge_graph(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取知识图谱（接口文档 10.1）。掌握度变化实时反映到节点着色。"""
    return success(kg_service.get_graph(db, user.id))
