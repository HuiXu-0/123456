"""AI 知识体系模块 Knowledge System（会话一录入·新增端点，向后兼容）。

区别于既有 10.1 知识图谱（`GET /knowledge-graph`，12 节点掌握度着色）：本端点暴露
《AI知识体系设计-v3》**78 点 / 7 板块完整目录**（结构与元信息，内容由生成引擎按需
产出）+ 当前用户**板块级能力覆盖**（已测 + 先修推断）。供后续「知识图谱分层可视化」
会话消费，不改动既有 30 接口签名。

需登录（coverage 依赖当前用户掌握度/能力分）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import success
from app.core.security import get_current_user
from app.models.entities import User
from app.services import knowledge_catalog
from app.services import mastery as mastery_service

router = APIRouter(tags=["knowledge-system"])


@router.get("/knowledge-system")
async def get_knowledge_system(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取 78 点 / 7 板块知识体系目录 + 当前用户板块级覆盖（会话一录入）。

    响应 data：
    - boards：7 板块 [{code, name, total, levels:{入门,进阶,前沿}}]；
    - points：78 点 [{id, code, name, category, board, level, prerequisites, description, isCore}]；
    - coverage：板块级能力覆盖 [{board, name, total, tested, inferred, covered, coveragePct}]
      （tested=已测掌握、inferred=先修推断、covered=二者并集）。
    """
    points = knowledge_catalog.list_catalog(db)
    boards = []
    for code, name in knowledge_catalog.BOARDS:
        in_board = [p for p in points if p["category"] == code]
        levels = {"入门": 0, "进阶": 0, "前沿": 0}
        for p in in_board:
            if p["level"] in levels:
                levels[p["level"]] += 1
        boards.append({"code": code, "name": name, "total": len(in_board), "levels": levels})
    status_map = mastery_service.get_status_map(db, user.id)
    score_map = mastery_service.get_score_map(db, user.id)
    coverage = knowledge_catalog.board_coverage(db, status_map, score_map)
    return success({"boards": boards, "points": points, "coverage": coverage})
