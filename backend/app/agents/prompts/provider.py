"""Prompt 统一解析层（R1：统一 Agent Framework）。

职责边界 —— **只解析，不生成**：

- **DB 优先**：``diagnosis`` / ``generation`` / ``critic`` 三个可热更模板优先读
  ``app.services.prompts``（每次现读，管理端 PUT 后下一次调用立即生效，禁止进程内缓存）；
- **Code 兜底**：DB 不可用（表缺失、连接异常、会话失效等）时回落 ``services.prompts``
  的代码侧默认模板。默认模板与 DB 种子值同源（同一份 ``_DEFAULTS``），渲染结果**逐字一致**
  —— 热更失效的代价是"回到默认"，而不是让生成链路失败；
- **LLMClient 归属声明**：``planner`` / ``dialogue`` / ``feynman`` / ``evaluation`` 四个
  Agent 的 system prompt 物理位置在 ``core/llm.py``（R1 明确不迁移、不复制），此处只登记
  ``owner`` 供 Registry 与 trace 内省，``source="llm_client"`` 且 ``available=False``——
  **不参与模板渲染**，避免出现第二份 prompt 真值源（这是 R5 的落点）。

不改任何业务生成路径：``LLMClient`` 各语义方法仍是这四个 Agent 的运行时权威。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.services import prompts as prompts_service

logger = logging.getLogger("app.agents.prompts")

# 占位符语法：{varName} —— 与改造前 app.agents.render_template 完全一致。
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# 可经模板渲染的来源（available=True）
_TEMPLATE_SOURCES = frozenset({"db", "code"})

# 归属 LLMClient 的 Agent：id → (展示名, 运行时权威)。
# 展示名与各 Agent 模块的 AGENT_NAME / 文档口径一致；core/llm.py 仍是唯一真值源。
_LLM_CLIENT_PROMPTS: dict[str, tuple[str, str]] = {
    "planner": ("学习路径规划Agent", "core.llm.LLMClient.plan_path"),
    "dialogue": ("对话式学情诊断Agent", "core.llm.LLMClient.extract_portrait"),
    "feynman": ("费曼讲解评估Agent", "core.llm.LLMClient.feynman_eval"),
    "evaluation": ("学习过程评估Agent", "core.llm.LLMClient.evaluate_learning"),
}


def render_template(template: str, variables: Mapping[str, Any]) -> str:
    """`{var}` 占位符直替渲染。

    只替换 ``variables`` 中存在的键（值转 str）；未知占位符原样保留。
    不用 ``str.format`` 以免模板中的普通花括号（如 JSON 示例）抛 KeyError。
    **语义与改造前 ``app.agents.render_template`` 逐字一致**——它是同一段逻辑的
    canonical 位置，``app.agents`` 继续 re-export，既有 ``from app.agents import
    render_template`` 调用方零改动。
    """

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(variables[key]) if key in variables else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, template)


@dataclass(frozen=True)
class PromptResolution:
    """一次 Prompt 解析结果（纯值对象，无副作用）。"""

    agent_id: str
    name: str
    template: str = ""
    variables: tuple[str, ...] = ()
    version: int | None = None
    source: str = "unknown"  # db | code | llm_client | unknown
    owner: str | None = None
    db_error: str | None = None

    @property
    def available(self) -> bool:
        """是否可做模板渲染（DB 或代码兜底）。"""
        return self.source in _TEMPLATE_SOURCES

    @property
    def hot_reloadable(self) -> bool:
        """是否为管理端 PUT 即生效的热更模板（仅 DB 来源）。"""
        return self.source == "db"

    def render(self, variables: Mapping[str, Any]) -> str:
        """渲染模板。不可渲染来源（llm_client / unknown）→ ValueError。"""
        if not self.available:
            raise ValueError(f"agentId 不可模板渲染：{self.agent_id}（source={self.source}）")
        return render_template(self.template, variables)

    def manifest(self) -> dict[str, Any]:
        """供 Registry / trace 内省的结构化描述。"""
        return {
            "agentId": self.agent_id,
            "name": self.name,
            "source": self.source,
            "owner": self.owner,
            "version": self.version,
            "variables": list(self.variables),
            "hotReloadable": self.hot_reloadable,
            "dbError": self.db_error,
        }


class PromptProvider:
    """Prompt 解析器：DB 优先 → 代码兜底 → LLMClient 归属声明。

    无状态，可复用单例；不缓存模板（热更语义要求每次现读）。
    """

    def resolve(self, db: Any, agent_id: str) -> PromptResolution:
        """解析 agentId 对应的 Prompt 来源。

        Args:
            db: SQLAlchemy Session；仅 DB 来源需要。
            agent_id: 目标 Agent 标识。

        Returns:
            PromptResolution；``available=False`` 表示不可模板渲染（调用方自行决定
            是否抛错——``run_agent`` 保持改造前的 ``未知 agentId`` 语义）。
        """
        agent_id = str(agent_id).strip()

        # ── 1. DB 优先（含 get-or-seed，未知 agentId 返回 None）──────────────
        row: dict[str, Any] | None = None
        db_error: str | None = None
        try:
            row = prompts_service.get_prompt(db, agent_id)
        except Exception as exc:  # noqa: BLE001 — DB 故障必须降级而非打断生成链路
            db_error = type(exc).__name__
            logger.warning(
                "prompt_db_unavailable agent_id=%s error_type=%s → 回落代码侧默认模板",
                agent_id,
                db_error,
            )
        if row is not None:
            return PromptResolution(
                agent_id=agent_id,
                name=str(row["name"]),
                template=str(row["template"]),
                variables=tuple(row.get("variables") or ()),
                version=row.get("version"),
                source="db",
                owner="services.prompts.PromptTemplate",
                db_error=db_error,
            )

        # ── 2. Code 兜底（DB 不可用，或该 id 有代码侧默认模板）───────────────
        default = prompts_service.get_default_prompt(agent_id)
        if default is not None:
            name, template, variables = default
            return PromptResolution(
                agent_id=agent_id,
                name=name,
                template=template,
                variables=tuple(variables),
                version=None,
                source="code",
                owner="services.prompts._DEFAULTS",
                db_error=db_error,
            )

        # ── 3. 归属 LLMClient 的 Agent（仅内省，不渲染）──────────────────────
        meta = _LLM_CLIENT_PROMPTS.get(agent_id)
        if meta is not None:
            name, owner = meta
            return PromptResolution(
                agent_id=agent_id,
                name=name,
                source="llm_client",
                owner=owner,
                db_error=db_error,
            )

        return PromptResolution(agent_id=agent_id, name=agent_id, db_error=db_error)


_provider: PromptProvider | None = None


def get_prompt_provider() -> PromptProvider:
    """进程内单例（与 ``get_llm()`` 同风格）。"""
    global _provider
    if _provider is None:
        _provider = PromptProvider()
    return _provider
