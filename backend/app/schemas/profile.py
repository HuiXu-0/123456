"""Profile / LearningPath 请求体校验（B2-a）。

字段名严格对齐《后端接口文档》4.2 / 4.3 / 6.2（camelCase 契约字段）。
parse 接口为 multipart，用 Form/File 解析，不在此建模。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FieldValue(BaseModel):
    """带来源的单值字段（接口文档 4.1 education/major/goal）。"""

    value: str
    source: str | None = None  # resume|ocr|text|manual


class SkillItem(BaseModel):
    """技能项（接口文档 4.1 skills）。name 取自固定 6 维。"""

    name: str
    level: int = 0  # 0-100
    source: str | None = None


class Material(BaseModel):
    """材料引用（接口文档 4.1 materials）。kind: doc|image|text。"""

    id: str
    label: str
    kind: str


class DraftProfile(BaseModel):
    """4.2 请求中的结构化画像草稿（4.1 ParsedProfile 子集）。"""

    education: FieldValue | None = None
    major: FieldValue | None = None
    goal: FieldValue | None = None
    skills: list[SkillItem] = Field(default_factory=list)


class TargetJob(BaseModel):
    """目标岗位（接口文档 4.2 targetJob，可为 null）。"""

    name: str
    radar: dict[str, int] = Field(default_factory=dict)


class NarrativeRequest(BaseModel):
    """POST /profile/narrative 请求体（接口文档 4.2）。"""

    draft: DraftProfile
    materials: list[Material] = Field(default_factory=list)
    targetJob: TargetJob | None = None


class DiagnosisCompleteRequest(BaseModel):
    """POST /profile/diagnosis-complete 请求体（接口文档 4.3）。"""

    targetJobName: str
    matchPct: int  # 0-100


class GeneratePathRequest(BaseModel):
    """POST /learning-path/generate 请求体（接口文档 6.2，targetJobId 可选）。"""

    targetJobId: str | None = None


class PortraitDimensionItem(BaseModel):
    """单个画像维度（接口文档 17.2 PortraitDimension，C2 三分类扩展）。

    简历 / 手动路径把表单输入映射为与对话诊断同一套 canonical key 后回写。
    updatedAt 由服务端统一加盖，请求体可不带。新增 kind/basis/optionKey 向后兼容
    （旧客户端不传则服务端按 key 自动归类、缺省为空）。
    """

    key: str
    label: str
    kind: str | None = None  # ability|preference|subjective（缺省服务端按 key 归类）
    value: str = ""
    score: int | None = None  # 仅 ability 维（如知识基础）0-100；偏好/主观维禁止打分
    basis: str | None = None  # ability 维「依据」：分数来自哪几道题 / 哪些作答（防臆造）
    optionKey: str | None = None  # preference 维类型码（图像型/稳步细钻型/概念混淆…）
    confidence: float = 0.6
    source: str = "manual"  # dialogue|manual|inferred|diagnostic


class StudentPortraitWriteRequest(BaseModel):
    """PUT /profile/student-portrait 请求体（接口文档 17.4，覆盖写入权威画像）。"""

    dimensions: list[PortraitDimensionItem] = Field(default_factory=list)


class DialogueRequest(BaseModel):
    """POST /profile/dialogue 请求体（接口文档 17.1，对话式画像诊断）。

    sessionId 首轮可空（后端生成 d_ 前缀 id）；message 学生自然语言输入；
    context 可选，首轮可带已知信息（major/goal，复用 4.1 枚举），多余键忽略。
    """

    sessionId: str | None = None
    message: str = ""
    # C2 三段式：当上一轮抛出微测题 / 偏好题时，answer 携带点选项的稳定值
    # （微测 = option_id；偏好 = optionKey）。自由文本作答可只传 message（服务端兜底归类）。
    answer: str | None = None
    context: dict[str, Any] | None = None
