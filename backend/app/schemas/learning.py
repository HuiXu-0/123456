"""学习流程模块请求体校验（接口文档第 18 章，C2）。

字段名严格对齐《后端接口文档》18.1（康奈尔线索）/ 18.2（费曼）/ 18.3（步骤）/
18.5（笔记保存）的 camelCase 契约字段。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CornellCuesRequest(BaseModel):
    """POST /learning/cornell-cues 请求体（接口文档 18.1）。"""

    kpId: str
    difficulty: str = "初级"  # 入门|初级|高级（与 8.2 讲义档一致），缺省「初级」


class FeynmanRequest(BaseModel):
    """POST /learning/feynman 请求体（接口文档 18.2）。"""

    kpId: str
    sessionId: str | None = None  # 多轮上下文 id，首轮可空（后端生成 f_ 前缀 id）
    explanation: str = ""  # 学生本轮讲解文本


class StepUpdateRequest(BaseModel):
    """POST /learning/steps/{kpId} 请求体（接口文档 18.3）。"""

    step: str  # video|lecture|diagram|note|feynman|practice
    done: bool = True  # 置为完成 / 取消完成


class CueNoteItem(BaseModel):
    """线索区单条作答（接口文档 18.4 cueNotes[]）。"""

    cueId: str | None = None
    text: str = ""


class NoteSaveRequest(BaseModel):
    """PUT /learning/notes/{kpId} 请求体（接口文档 18.5，整体覆盖保存）。"""

    cueNotes: list[CueNoteItem] = Field(default_factory=list)
    mainNotes: str = ""
    summary: str = ""
