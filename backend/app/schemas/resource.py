"""Resource / Quiz 请求体校验（B2-b）。

字段名严格对齐《后端接口文档》8.2（讲义）/ 9.1（测验提交）的 camelCase / snake_case
契约字段：讲义请求用 camelCase（kpId/difficulty），测验题字段沿用 snake_case
（question_id 等，与 2.5 QuizQuestion 一致）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LectureRequest(BaseModel):
    """POST /resource/lecture 请求体（接口文档 8.2）。"""

    kpId: str
    difficulty: str  # 入门|初级|高级（资源页难度档，与路径难度档不同）


class VideoRequest(BaseModel):
    """POST /resource/video 请求体（接口文档 8.3，B7-a）。"""

    kpId: str
    difficulty: str  # 入门|初级|高级（与讲义同档）


class VideoRenderRequest(BaseModel):
    """POST /resource/video/render 请求体（接口文档 8.3 增量，CC-video-mp4 第二步）。"""

    kpId: str
    difficulty: str  # 入门|初级|高级（与 8.3 同档）


class TutorChatRequest(BaseModel):
    """POST /resource/tutor/chat 请求体（接口文档 8.7，B7-a）。"""

    kpId: str
    sessionId: str | None = None  # 可空，首轮由后端生成
    message: str


class TutorSuggestRequest(BaseModel):
    """POST /resource/tutor/suggest 请求体（接口文档 8.8，C-fix 批3-bonus）。"""

    kpId: str
    question: str  # 学生的困惑/提问（或"我没懂"上下文）


class TutorGenerateRequest(BaseModel):
    """POST /resource/tutor/generate 请求体（接口文档 8.8）。"""

    kpId: str
    problemPoint: str | None = None  # suggest 返回的问题点，可空（缺省按知识点核心概念）
    types: list[str] = Field(default_factory=list)  # 勾选的资源类型子集


class ExternalAggregateRequest(BaseModel):
    """POST /resource/external/aggregate 请求体（接口文档 8.6 增量）。"""

    kpId: str
    query: str | None = None  # 自定义检索式，可空（缺省由知识点 + 薄弱点构造）
    weakPoints: list[str] = Field(default_factory=list)  # 薄弱点，可空（缺省由 Mastery 派生）


class TTSSynthesizeRequest(BaseModel):
    """POST /tts/synthesize 请求体（接口文档 8.9，语音合成）。

    voice/rate/pitch 可空——缺省用配置的自然中文女声（XiaoyiNeural / -10% / +2Hz）。
    """

    text: str
    voice: str | None = None
    rate: str | None = None  # edge-tts 语速，如 "-10%"
    pitch: str | None = None  # edge-tts 音调，如 "+2Hz"


class QuizAnswerItem(BaseModel):
    """单题作答（接口文档 9.1）。answer：single/boolean 为 str，multiple 为 str[]。"""

    question_id: str
    answer: str | list[str]


class QuizSubmitRequest(BaseModel):
    """POST /quiz/{kpId}/submit 请求体（接口文档 9.1）。"""

    answers: list[QuizAnswerItem] = Field(default_factory=list)


class ReinforceRequest(BaseModel):
    """POST /reinforce 请求体（接口文档 9.2）。"""

    kpId: str
    wrongQuestionIds: list[str] = Field(default_factory=list)
