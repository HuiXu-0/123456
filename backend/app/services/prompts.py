"""Prompt 模板服务（B4-b，接口文档 14.5）。

- agentId 固定 3 项：diagnosis / generation / critic（与 11.2 agents[].id 对齐）；
- 默认模板懒种子（get-or-create，幂等），版本从 1 起；
- PUT 保存即热更新：模板存 SQLite，B5 生成链路经 get_template() 每次现读，
  无进程内缓存 → 保存后下一次生成调用立即生效（无需重启）；
- variables 为该 Agent 的必需占位符清单（契约定死），更新时校验全部保留。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import PromptTemplate

# 占位符语法：{varName}
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# ---- generation 模板（CC 内容质量提升：递进结构 + 标准 LaTeX + 贴画像深度） ----------
# 在 B8「接地可校验」基础上增强：①六段递进结构（概念→原理→例子→代码→误区→小结）；
# ②公式一律标准 LaTeX（$...$ / $$...$$，交前端 KaTeX 渲染）；③新增 {learnerProfile}
# 占位符，让同一知识点对能力强 / 零基础用户产出深度不同的讲义。防幻觉铁律不变（不绕过 Critic）。
_GENERATION_TEMPLATE = (
    "你是领域知识讲义生成专家，依据检索资料生成与学习者难度、能力画像适配的高质量课程讲义。\n"
    "铁律（防幻觉）：讲义中每一个事实、定义、公式与结论都必须来自下方检索资料，"
    "只做重组、改写、推导展开与详略取舍，措辞尽量贴近资料原文；"
    "比喻、例子、代码同样只能取自资料或资料的直接逻辑推论，严禁自创资料之外的事实与数据；"
    "资料未覆盖的内容宁可从简或不写。\n"
    "知识点：{kpName}\n"
    "必须覆盖的核心概念：{description}\n"
    "难度：{difficulty}\n"
    "学习者画像：{learnerProfile}\n"
    "结构要求（按此递进组织，不要平铺罗列）：①概念引入（要解决什么问题、给直觉/类比）→"
    "②核心原理（含必要公式）→③具体例子（每个关键概念至少配一个）→④代码示例"
    "（带注释、注明输入与输出；涉及算法/实现处给可运行片段）→⑤常见误区→⑥小结。\n"
    "公式格式：一律用标准 LaTeX——行内 $...$、独立公式 $$...$$，规范转义"
    "（如 \\frac、\\sqrt、\\sum、上标 ^、下标 _）；禁止用 \\(...\\) 或纯文本符号堆叠。\n"
    "表格格式：凡需对比/罗列多维信息（如多种方法/激活函数/优缺点对照）一律用标准 GFM 表格——"
    "首行表头、第二行用 | --- | --- | 分隔线，每行单元格数与表头一致，单元格内不换行；"
    "禁止用纯文本对齐空格或制表符伪造表格。\n"
    "贴画像给不同深度：依据上面的「学习者画像」调节深度——能力强者补公式推导、底层原理与"
    "对比延伸，信息密度高；零基础者多用类比、少堆术语、步骤更细、先直觉后形式化；"
    "难度档在此基础上叠加，使同一知识点对不同用户产出深度不同的讲义。\n"
    "检索资料：{ragContext}\n"
    "请输出 Markdown 讲义，按上述六段递进结构组织，覆盖全部核心概念。"
)

# generation 历史默认模板（仅当库内仍是某历史默认、未被管理员 PUT 改过时，自动升级到最新默认；
# 管理员自定义模板不在此集合，故升级逻辑不会覆盖人工改动）。
_GENERATION_V1 = (
    "你是领域知识讲义生成专家，依据检索资料生成与学习者难度适配的讲义。\n"
    "铁律：讲义中每一个事实、定义、公式与结论都必须来自下方检索资料，"
    "只做重组、改写与详略取舍，措辞尽量贴近资料原文；"
    "比喻、例子、代码同样只能取自资料，严禁自创类比或引入资料之外的事实与数据；"
    "资料未覆盖的内容宁可从简或不写。\n"
    "知识点：{kpName}\n"
    "必须覆盖的核心概念：{description}\n"
    "难度：{difficulty}\n"
    "难度风格约定——入门：用资料中的比喻与直白讲解建立直觉，不出现代码与数学公式，"
    "篇幅最短；初级：核心概念讲解 + 资料中的基础代码示例，仅保留必要公式，篇幅适中；"
    "高级：数学形式化表达 + 代码实现 + 资料中的工程权衡与优化细节，信息密度最高。\n"
    "检索资料：{ragContext}\n"
    "请输出 Markdown 讲义：概念讲解、示例、要点小结，并覆盖上述核心概念。"
)

# _GENERATION_V2：在 _GENERATION_TEMPLATE 加入「表格格式」铁律前的历史默认（六段递进 + LaTeX，无表格约定）。
# 命中即可安全升级到当前默认，使已落库的旧默认自动获得 GFM 表格规范，且不覆盖管理员人工改动。
_GENERATION_V2 = (
    "你是领域知识讲义生成专家，依据检索资料生成与学习者难度、能力画像适配的高质量课程讲义。\n"
    "铁律（防幻觉）：讲义中每一个事实、定义、公式与结论都必须来自下方检索资料，"
    "只做重组、改写、推导展开与详略取舍，措辞尽量贴近资料原文；"
    "比喻、例子、代码同样只能取自资料或资料的直接逻辑推论，严禁自创资料之外的事实与数据；"
    "资料未覆盖的内容宁可从简或不写。\n"
    "知识点：{kpName}\n"
    "必须覆盖的核心概念：{description}\n"
    "难度：{difficulty}\n"
    "学习者画像：{learnerProfile}\n"
    "结构要求（按此递进组织，不要平铺罗列）：①概念引入（要解决什么问题、给直觉/类比）→"
    "②核心原理（含必要公式）→③具体例子（每个关键概念至少配一个）→④代码示例"
    "（带注释、注明输入与输出；涉及算法/实现处给可运行片段）→⑤常见误区→⑥小结。\n"
    "公式格式：一律用标准 LaTeX——行内 $...$、独立公式 $$...$$，规范转义"
    "（如 \\frac、\\sqrt、\\sum、上标 ^、下标 _）；禁止用 \\(...\\) 或纯文本符号堆叠。\n"
    "贴画像给不同深度：依据上面的「学习者画像」调节深度——能力强者补公式推导、底层原理与"
    "对比延伸，信息密度高；零基础者多用类比、少堆术语、步骤更细、先直觉后形式化；"
    "难度档在此基础上叠加，使同一知识点对不同用户产出深度不同的讲义。\n"
    "检索资料：{ragContext}\n"
    "请输出 Markdown 讲义，按上述六段递进结构组织，覆盖全部核心概念。"
)

# agentId -> 历史默认模板集合（命中即可安全升级到 _DEFAULTS 的当前默认）
_PRIOR_DEFAULTS: dict[str, set[str]] = {"generation": {_GENERATION_V1, _GENERATION_V2}}


# 默认模板（agentId -> (name, template, variables)）。
# name 与接口文档 11.2 agents[].name 逐字一致；generation 模板与 14.5 示例同构。
_DEFAULTS: dict[str, tuple[str, str, list[str]]] = {
    "diagnosis": (
        "学情诊断Agent",
        "你是学情诊断专家，负责从学习者画像与掌握度数据中定位薄弱知识点。\n"
        "学习者画像：{profileSummary}\n"
        "知识点掌握度：{masteryStatus}\n"
        "目标岗位：{targetJob}\n"
        "请输出：薄弱知识点清单（按优先级排序）、诊断依据、建议学习顺序。",
        ["profileSummary", "masteryStatus", "targetJob"],
    ),
    "generation": (
        "领域知识生成Agent",
        _GENERATION_TEMPLATE,
        ["kpName", "description", "difficulty", "ragContext", "learnerProfile"],
    ),
    "critic": (
        "内容审核校验Agent",
        "你是内容审核校验专家，对生成的讲义逐句做 RAG 交叉校验，标记未接地句子。\n"
        "待审内容：{draftContent}\n"
        "检索来源切片：{ragContext}\n"
        "请输出：逐句接地判定、疑似幻觉句清单、整体幻觉率（未接地句数/总句数）。",
        ["draftContent", "ragContext"],
    ),
}

AGENT_IDS: tuple[str, ...] = tuple(_DEFAULTS)


class MissingPlaceholderError(ValueError):
    """新模板缺失必需占位符（→ 1001/400）。"""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"模板缺失必需占位符：{', '.join(missing)}")


def _get_or_seed(db: Session, agent_id: str) -> PromptTemplate | None:
    """按 agentId 取模板；首次访问时落默认种子（幂等）。未知 agentId → None。"""
    if agent_id not in _DEFAULTS:
        return None
    name, template, variables = _DEFAULTS[agent_id]
    row = db.get(PromptTemplate, agent_id)
    if row is None:
        row = PromptTemplate(
            agent_id=agent_id,
            name=name,
            template=template,
            variables=list(variables),
            version=1,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    elif row.template in _PRIOR_DEFAULTS.get(agent_id, set()) and row.template != template:
        # 库内仍是历史默认（未被管理员 PUT 改过）→ 平滑升级到当前默认，保留人工改动不被覆盖。
        row.template = template
        row.variables = list(variables)
        row.version += 1
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
    return row


def _to_dict(row: PromptTemplate) -> dict[str, Any]:
    """PromptTemplate → 接口文档 14.5 响应 data（camelCase）。"""
    return {
        "agentId": row.agent_id,
        "name": row.name,
        "template": row.template,
        "variables": list(row.variables or []),
        "version": row.version,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_default_prompt(agent_id: str) -> tuple[str, str, list[str]] | None:
    """代码侧默认模板（R1：PromptProvider 的 DB 兜底读取口）。

    与 ``_get_or_seed`` 种子值**同源同份**——DB 拿不到时回落本函数，渲染结果与
    从 DB 读到的默认模板逐字一致，因此兜底不会改变输出。

    Returns:
        ``(name, template, variables)``；未知 agentId → None。
    """
    entry = _DEFAULTS.get(agent_id)
    if entry is None:
        return None
    name, template, variables = entry
    return name, template, list(variables)


def get_prompt(db: Session, agent_id: str) -> dict[str, Any] | None:
    """14.5 GET：完整 PromptTemplate。未知 agentId → None。"""
    row = _get_or_seed(db, agent_id)
    return _to_dict(row) if row is not None else None


def get_template(db: Session, agent_id: str) -> str | None:
    """供 B5 生成链路消费的读取路径：每次现读 DB，PUT 后立即生效（热更新）。"""
    row = _get_or_seed(db, agent_id)
    return row.template if row is not None else None


def update_prompt(db: Session, agent_id: str, template: str) -> dict[str, Any] | None:
    """14.5 PUT：校验占位符 → 版本自增 → 保存即热更新。

    Returns:
        None：未知 agentId（→ 1004）；
        dict：{agentId, version, updatedAt, hotReloaded}。
    Raises:
        MissingPlaceholderError：缺失必需占位符（→ 1001）。
    """
    row = _get_or_seed(db, agent_id)
    if row is None:
        return None
    present = set(_PLACEHOLDER_RE.findall(template))
    missing = [v for v in (row.variables or []) if v not in present]
    if missing:
        raise MissingPlaceholderError(missing)
    row.template = template
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return {
        "agentId": row.agent_id,
        "version": row.version,
        "updatedAt": row.updated_at.isoformat(),
        "hotReloaded": True,
    }
