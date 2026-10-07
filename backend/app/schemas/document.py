"""「文档学习」请求体（接口文档第 20 章）。

上传走 multipart（UploadFile，见 api/v1/document.py），生成类走 JSON body。
字段 camelCase 对齐前端；难度枚举复用讲义档（入门|初级|中级|高级|精通）。

多文档统一生成（20.5 增补）：生成类请求新增可选 `documentIds`（多篇文档合并检索范围统一生成）。
未传 `documentIds` 时回退单篇 `documentId`（向后兼容，行为不变）；两者同传以 `documentIds` 为准。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class _DocGenBase(BaseModel):
    """基于文档的生成请求基类：单篇 documentId（必填、向后兼容）+ 可选多篇 documentIds。

    产物落库（增补，向后兼容）：可选 ``regenerate``——缺省 ``false`` 时「查看」优先读已落库
    产物、命中不重生成；显式 ``true`` 时强制重跑生成并覆盖产物、刷新时间（「重新生成」动作）。
    """

    documentId: str = Field(..., min_length=1)
    documentIds: list[str] | None = Field(default=None)
    regenerate: bool = Field(default=False)

    def doc_ids(self) -> list[str]:
        """归一为文档 id 列表：优先 documentIds（去空），否则回退 [documentId]。"""
        ids = [d for d in (self.documentIds or []) if d]
        return ids or [self.documentId]


class DocLectureRequest(_DocGenBase):
    difficulty: str = "初级"


class DocVideoRequest(_DocGenBase):
    difficulty: str = "初级"


class DocOverviewRequest(BaseModel):
    # 概览为「关于单篇来源」的速读，不做多篇合并
    documentId: str = Field(..., min_length=1)


class DocDiagramRequest(_DocGenBase):
    pass


class DocMindmapRequest(_DocGenBase):
    pass


class DocQuizRequest(_DocGenBase):
    count: int = Field(default=5, ge=1, le=20)


class DocFlashcardRequest(_DocGenBase):
    count: int = Field(default=8, ge=1, le=30)


class DocChatRequest(BaseModel):
    """和文档对话（接口文档 20.8）：就选中文档（可多篇）提问，严格基于文档流式作答 + 溯源。"""

    documentId: str = Field(..., min_length=1)
    documentIds: list[str] | None = Field(default=None)
    sessionId: str | None = Field(default=None)
    message: str = Field(..., min_length=1)

    def doc_ids(self) -> list[str]:
        ids = [d for d in (self.documentIds or []) if d]
        return ids or [self.documentId]
