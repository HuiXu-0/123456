"""Learning Tools（R4.2）：把既有业务能力暴露为标准 Tool。

## 三个 Tool 与它们的**唯一**实现来源

| 注册名（= 权限键） | 操作 | 复用 | 是否重写算法 |
|---|---|---|---|
| `learning/profile` | `get_learning_profile` | **`services.student_portrait.get_portrait`** | ❌ 否，画像计算原样复用 |
| `learning/mastery` | `get_mastery_state` | **`services.mastery.get_status_map` / `get_score_map`** | ❌ 否，掌握度口径原样复用 |
| `learning/path` | `get_learning_path` | **`agents.planner_agent.plan_path()`** | ❌ 否，排序/先修闭包/板块裁剪/时长估算原样复用 |

本模块**只做**：参数校验 → 开关 Session → 调既有函数 → 打包成 JSON。

## Session 生命周期

`_DbTool._with_session(fn)` 内部自开自关 Session 并把 Session **只传给闭包** ——
Session 既不出现在 Tool 的公开属性上，也不会进返回值（"Tool 不直接暴露 DB Session"）。

## 确定性

`get_learning_path` 的 `narrate` **默认 False** ——
`plan_path(narrate=False)` 是"强制确定性模板、零网络"路径（planner 自己的文档口径），
因此**同参数必得同结果**，与当前 provider 是 mock 还是 deepseek 无关。

## Mock

三个 `mock_*` 返回固定内容；真实路径因基础设施异常失败时回落 mock 并标
``degraded=True``（记日志，不静默）。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents import planner_agent
from app.agents.tools.registry import (
    LEARNING_MASTERY_OPERATION,
    LEARNING_MASTERY_TOOL,
    LEARNING_PATH_OPERATION,
    LEARNING_PATH_TOOL,
    LEARNING_PROFILE_OPERATION,
    LEARNING_PROFILE_TOOL,
    AgentTool,
    ToolSpec,
    get_tool_registry,
)
from app.core.database import SessionLocal
from app.services import mastery as mastery_service
from app.services import student_portrait as portrait_service

logger = logging.getLogger("app.agents.tools.learning")

T = TypeVar("T")


# ── 入参 Schema ──────────────────────────────────────────────────────────────


class GetLearningProfileInput(BaseModel):
    """``learning/profile`` 入参。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=64, description="目标用户（必须与 Tool 绑定身份一致）")


class GetMasteryStateInput(BaseModel):
    """``learning/mastery`` 入参。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=64, description="目标用户（必须与 Tool 绑定身份一致）")
    kp_id: str | None = Field(default=None, max_length=32, description="可选：只看某个知识点")


class GetLearningPathInput(BaseModel):
    """``learning/path`` 入参。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=64, description="目标用户（必须与 Tool 绑定身份一致）")
    target_job_id: str | None = Field(default=None, max_length=64, description="可选：目标岗位 id")
    narrate: bool = Field(
        default=False,
        description="是否用 LLM 生成理由叙述；默认 False = 确定性模板、零网络、可重复",
    )


# ── Mock（确定性，无随机无网络）───────────────────────────────────────────────

_MOCK_LESSONS: list[dict[str, Any]] = [
    {"sequence": 1, "topic": "机器学习基础", "difficulty": "入门", "status": "completed",
     "progress": 100, "kpId": "ml", "estimatedMinutes": 60, "reason": "先修骨架起点"},
    {"sequence": 2, "topic": "神经网络", "difficulty": "初级", "status": "learning",
     "progress": 40, "kpId": "nn", "estimatedMinutes": 60, "reason": "薄弱点优先"},
    {"sequence": 3, "topic": "深度学习", "difficulty": "初级", "status": "not-started",
     "progress": 0, "kpId": "dl", "estimatedMinutes": 105, "reason": "先修依赖 nn"},
]


def mock_get_learning_profile(*, user_id: str) -> dict[str, Any]:
    return {
        "tool": LEARNING_PROFILE_TOOL,
        "operation": LEARNING_PROFILE_OPERATION,
        "mock": True,
        "userId": user_id,
        "portrait": {
            "userId": user_id,
            "dimensions": [
                {"key": "learning_goal", "label": "学习目标", "value": "掌握 Transformer",
                 "confidence": 0.8, "source": "dialogue"},
                {"key": "cognitive_style", "label": "认知风格", "value": "图像型",
                 "optionKey": "visual", "confidence": 0.9, "source": "dialogue"},
            ],
            "version": "v1",
        },
    }


def mock_get_mastery_state(*, user_id: str, kp_id: str | None = None) -> dict[str, Any]:
    status = {"ml": "passed", "nn": "learning", "dl": "not-started"}
    scores = {"ml": {"score": 92, "source": "quiz"},
              "nn": {"score": 60, "source": "diagnostic"}}
    if kp_id:
        status = {k: v for k, v in status.items() if k == kp_id}
        scores = {k: v for k, v in scores.items() if k == kp_id}
    return {
        "tool": LEARNING_MASTERY_TOOL,
        "operation": LEARNING_MASTERY_OPERATION,
        "mock": True,
        "userId": user_id,
        "kpId": kp_id,
        "status": status,
        "scores": scores,
    }


def mock_get_learning_path(
    *, user_id: str, target_job_id: str | None = None, narrate: bool = False
) -> dict[str, Any]:
    lessons = [dict(item) for item in _MOCK_LESSONS if item["sequence"] <= 3]
    return {
        "tool": LEARNING_PATH_TOOL,
        "operation": LEARNING_PATH_OPERATION,
        "mock": True,
        "userId": user_id,
        "targetJobId": target_job_id,
        "narrate": narrate,
        "lessons": lessons,
        "summary": f"mock 路径：{len(lessons)} 个知识点（确定性模板）",
    }


# ── Tool 基类 ────────────────────────────────────────────────────────────────


class _DbTool(AgentTool):
    """需要 DB 的 Tool 基类：**Session 只在闭包内存活**，不对外暴露。"""

    def __init__(self, *, user_id: str, session_factory: Callable[[], Session] | None = None) -> None:
        super().__init__(user_id=user_id)
        self._session_factory = session_factory or SessionLocal

    @property
    def session_factory(self) -> Callable[[], Session]:
        """仅供测试注入；返回的是**工厂**而非 Session 实例。"""
        return self._session_factory

    def _with_session(self, fn: Callable[[Session], T]) -> T:
        """自开自关 Session，并把它**只传给闭包** —— 不落属性、不进返回值。"""
        db = self._session_factory()
        try:
            return fn(db)
        finally:
            db.close()

    def _degrade(self, data: BaseModel, mock_result: dict[str, Any], exc: Exception) -> dict[str, Any]:
        logger.warning(
            "tool_%s_degraded error_type=%s → 回落 mock",
            self.name,
            type(exc).__name__,
        )
        payload = dict(mock_result)
        payload["degraded"] = True
        payload["errorType"] = type(exc).__name__
        return payload


# ── learning/profile ─────────────────────────────────────────────────────────


class GetLearningProfileTool(_DbTool):
    name = LEARNING_PROFILE_TOOL
    description = (
        "读取指定用户的学习者画像（六维异质画像：学习目标/先验经验/认知风格/"
        "学习节奏等维度与置信度）。复用既有画像服务，不重新计算。"
    )
    args_schema = GetLearningProfileInput
    operation = LEARNING_PROFILE_OPERATION

    def _run(self, data: GetLearningProfileInput) -> dict[str, Any]:
        try:
            portrait = self._with_session(
                lambda db: portrait_service.get_portrait(db, data.user_id)
            )
        except Exception as exc:  # noqa: BLE001
            return self._degrade(data, mock_get_learning_profile(user_id=data.user_id), exc)
        return {
            "tool": self.name,
            "operation": self.operation,
            "mock": False,
            "userId": data.user_id,
            "portrait": portrait,
        }


# ── learning/mastery ─────────────────────────────────────────────────────────


class GetMasteryStateTool(_DbTool):
    name = LEARNING_MASTERY_TOOL
    description = (
        "读取指定用户的知识点掌握状态与能力分（status/score/scoreSource）。"
        "复用既有掌握度服务口径，不重新实现掌握度算法。"
    )
    args_schema = GetMasteryStateInput
    operation = LEARNING_MASTERY_OPERATION

    def _run(self, data: GetMasteryStateInput) -> dict[str, Any]:
        try:
            def _read(db: Session) -> tuple[dict[str, Any], dict[str, Any]]:
                # 复用既有 service（口径与/ mastery、/resource 等接口完全一致）
                status = mastery_service.get_status_map(db, data.user_id)
                scores = mastery_service.get_score_map(db, data.user_id)
                if data.kp_id:
                    status = {k: v for k, v in status.items() if k == data.kp_id}
                    scores = {k: v for k, v in scores.items() if k == data.kp_id}
                return status, scores

            status, scores = self._with_session(_read)
        except Exception as exc:  # noqa: BLE001
            return self._degrade(
                data,
                mock_get_mastery_state(user_id=data.user_id, kp_id=data.kp_id),
                exc,
            )
        return {
            "tool": self.name,
            "operation": self.operation,
            "mock": False,
            "userId": data.user_id,
            "kpId": data.kp_id,
            "status": status,
            "scores": scores,
        }


# ── learning/path ────────────────────────────────────────────────────────────


class GetLearningPathTool(_DbTool):
    name = LEARNING_PATH_TOOL
    description = (
        "按用户画像/掌握度/目标岗位生成个性化学习路径（含顺序、难度、时长与每步资源）。"
        "直接复用既有规划算法；narrate=false 时为确定性模板、零网络。"
    )
    args_schema = GetLearningPathInput
    operation = LEARNING_PATH_OPERATION

    def _run(self, data: GetLearningPathInput) -> dict[str, Any]:
        try:
            plan = self._with_session(
                lambda db: planner_agent.plan_path(
                    db,
                    user_id=data.user_id,
                    target_job_id=data.target_job_id,
                    narrate=data.narrate,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return self._degrade(
                data,
                mock_get_learning_path(
                    user_id=data.user_id,
                    target_job_id=data.target_job_id,
                    narrate=data.narrate,
                ),
                exc,
            )
        return {
            "tool": self.name,
            "operation": self.operation,
            "mock": False,
            "userId": data.user_id,
            "targetJobId": data.target_job_id,
            "narrate": data.narrate,
            "lessons": plan.get("lessons") or [],
            "summary": plan.get("summary") or "",
        }


# ── 注册 ─────────────────────────────────────────────────────────────────────

def _specs() -> tuple[ToolSpec, ...]:
    """三个 learning Tool 的注册描述（工厂只收 ``user_id``，身份由此注入）。"""
    return (
        ToolSpec(
            name=LEARNING_PROFILE_TOOL,
            description=GetLearningProfileTool.description,
            args_schema=GetLearningProfileInput,
            factory=lambda *, user_id: GetLearningProfileTool(user_id=user_id),
            operation=LEARNING_PROFILE_OPERATION,
        ),
        ToolSpec(
            name=LEARNING_MASTERY_TOOL,
            description=GetMasteryStateTool.description,
            args_schema=GetMasteryStateInput,
            factory=lambda *, user_id: GetMasteryStateTool(user_id=user_id),
            operation=LEARNING_MASTERY_OPERATION,
        ),
        ToolSpec(
            name=LEARNING_PATH_TOOL,
            description=GetLearningPathTool.description,
            args_schema=GetLearningPathInput,
            factory=lambda *, user_id: GetLearningPathTool(user_id=user_id),
            operation=LEARNING_PATH_OPERATION,
        ),
    )


for _spec in _specs():
    get_tool_registry().register_spec(_spec)
