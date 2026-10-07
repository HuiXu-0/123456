"""现有Agent到BaseAgent框架的适配层（TASK-002-B2；R1 扩至全部 7 个业务 Agent）。

本模块只转换输入输出并委托旧函数，不复制或改写任何业务逻辑。数据库会话由调用方
管理；Adapter不创建、不提交、不关闭Session。

R1 增补：
- 既有 4 个 Adapter 补 ``capabilities`` / ``tools`` / ``prompt_id`` 声明
  （``BaseAgent`` 新增字段，见 ``app.agents.base``）；
- 新增 ``PathPlanningAgentAdapter`` / ``DialogueAgentAdapter`` /
  ``FeynmanAgentAdapter``，使 planner / dialogue / feynman 也进入统一执行框架；
  evaluation 此前已有 ``EvaluationAgentAdapter``（TASK-002-B2），无需新增；
- ``DialogueAgentAdapter`` 是**状态机单入口分发**：把 ``dialogue_agent`` 的十个纯
  逻辑函数按 ``operation`` 1:1 委托，**不搬运 ``services.profile_dialogue`` 的会话
  编排**（那属于业务逻辑）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel
from sqlalchemy.orm import Session

from app.agents import (
    critic_agent,
    diagnostic_agent,
    dialogue_agent,
    evaluation_agent,
    feynman_agent,
    generator_agent,
    planner_agent,
)
from app.agents.base import BaseAgent
from app.agents.state import AgentState
from app.agents.tools.registry import (
    LEARNING_MASTERY_TOOL,
    LEARNING_PATH_TOOL,
    LEARNING_PROFILE_TOOL,
    MEMORY_SEARCH_TOOL,
    RAG_TOOL_NAME,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LearningDiagnosisInput(_StrictModel):
    kp_id: str
    kp_name: str = ""
    profile_summary: str = (
        "六维能力画像：机器学习基础/神经网络较强，注意力机制/Transformer/大模型微调偏弱"
    )
    mastery_status: str = "{}"
    target_job: str = "大模型应用工程师"


class LegacyAgentOutput(_StrictModel):
    """diagnostic/generator旧入口共同的四字段返回结构。"""

    agentId: str
    name: str
    prompt: str
    output: dict[str, Any]


class ResourceGenerationInput(_StrictModel):
    kp_name: str
    difficulty: str
    rag_context: list[dict[str, Any]] = Field(default_factory=list)
    feedback: str | None = None
    description: str = ""
    learner_profile: str = ""
    depth_tier: str | None = None


class EvaluationInput(_StrictModel):
    user_id: str


class EvaluationOutput(RootModel[dict[str, Any]]):
    """保留旧Dashboard评估结果的顶层字典形状。"""


class QualityDecision(str, Enum):
    """资源质量路由决定；值保持大写，便于审计与条件边显式匹配。"""

    PASS = "PASS"
    REVISE = "REVISE"
    FALLBACK = "FALLBACK"


class CriticInput(_StrictModel):
    learning_goal: str
    knowledge_state: dict[str, Any] = Field(default_factory=dict)
    resources: list[dict[str, Any]] = Field(min_length=1)


class QualityDecisionOutput(_StrictModel):
    decision: QualityDecision
    passed: bool
    validation_score: float
    hallucination_rate: float
    issues: list[str] = Field(default_factory=list)
    reason: str


class LearningDiagnosisAgentAdapter(
    BaseAgent[LearningDiagnosisInput, LegacyAgentOutput]
):
    agent_name = "learning_diagnosis"
    description = "适配现有学情诊断Agent，不改变诊断业务逻辑"
    input_schema = LearningDiagnosisInput
    output_schema = LegacyAgentOutput
    capabilities = (
        "weak_point_detection",
        "priority_ranking",
        "portrait_reading",
        "mastery_reading",
    )
    tools: tuple[str, ...] = ()
    prompt_id = "diagnosis"

    def __init__(self, db: Session, **kwargs: Any) -> None:
        self.db = db
        super().__init__(**kwargs)

    def execute(
        self, agent_input: LearningDiagnosisInput, state: AgentState
    ) -> LegacyAgentOutput:
        result = diagnostic_agent.run_diagnostic(
            self.db,
            kp_id=agent_input.kp_id,
            kp_name=agent_input.kp_name,
            profile_summary=agent_input.profile_summary,
            mastery_status=agent_input.mastery_status,
            target_job=agent_input.target_job,
        )
        return LegacyAgentOutput.model_validate(result)


class ResourceGenerationAgentAdapter(
    BaseAgent[ResourceGenerationInput, LegacyAgentOutput]
):
    agent_name = "resource_generation"
    description = "适配现有资源生成Agent，不改变RAG约束和生成业务逻辑"
    input_schema = ResourceGenerationInput
    output_schema = LegacyAgentOutput
    capabilities = (
        "rag_grounded_generation",
        "learner_adaptive_depth",
        "feedback_revision",
    )
    # R4.2：**能力声明**（最小权限：只开这 4 个）。
    # R4.3：追加 trusted_rag（规格 §7 明确 generation 可访问），仍不越出本 Agent。
    # ⚠️ 只声明 —— 是否真进 Tool Loop 由调用方决定（R4.3 起才由 ToolLoop 消费）。
    tools: tuple[str, ...] = (
        MEMORY_SEARCH_TOOL,
        LEARNING_PROFILE_TOOL,
        LEARNING_MASTERY_TOOL,
        LEARNING_PATH_TOOL,
        RAG_TOOL_NAME,
    )
    prompt_id = "generation"

    def __init__(self, db: Session, **kwargs: Any) -> None:
        self.db = db
        super().__init__(**kwargs)

    def execute(
        self, agent_input: ResourceGenerationInput, state: AgentState
    ) -> LegacyAgentOutput:
        result = generator_agent.run_generator(
            self.db,
            kp_name=agent_input.kp_name,
            difficulty=agent_input.difficulty,
            rag_context=agent_input.rag_context,
            feedback=agent_input.feedback,
            description=agent_input.description,
            learner_profile=agent_input.learner_profile,
            depth_tier=agent_input.depth_tier,
        )
        return LegacyAgentOutput.model_validate(result)


class EvaluationAgentAdapter(BaseAgent[EvaluationInput, EvaluationOutput]):
    agent_name = "evaluation"
    description = "适配现有学习过程评估Agent，不改变指标和建议生成逻辑"
    input_schema = EvaluationInput
    output_schema = EvaluationOutput
    capabilities = (
        "behavior_signal_gathering",
        "multi_dimension_metrics",
        "adjustment_suggestion",
    )
    tools: tuple[str, ...] = ()
    prompt_id = "evaluation"

    def __init__(self, db: Session, **kwargs: Any) -> None:
        self.db = db
        super().__init__(**kwargs)

    def execute(
        self, agent_input: EvaluationInput, state: AgentState
    ) -> EvaluationOutput:
        result = evaluation_agent.evaluate(self.db, agent_input.user_id)
        return EvaluationOutput.model_validate(result)


class CriticAgentAdapter(BaseAgent[CriticInput, QualityDecisionOutput]):
    agent_name = "quality_critic"
    description = "适配现有内容审核Agent并输出结构化质量路由决定"
    input_schema = CriticInput
    output_schema = QualityDecisionOutput
    capabilities = (
        "sentence_grounding_check",
        "hallucination_rate",
        "quality_gate",
    )
    # R4.2：**能力声明**（最小权限：比 generation 少一个 learning/path ——
    # 审核方不需要生成路径）。⚠️ 只声明、不执行，本阶段无 Tool Loop。
    tools: tuple[str, ...] = (
        MEMORY_SEARCH_TOOL,
        LEARNING_PROFILE_TOOL,
        LEARNING_MASTERY_TOOL,
        RAG_TOOL_NAME,  # R4.3 规格 §7：critic 可访问可信检索
    )
    prompt_id = "critic"

    def __init__(self, db: Session, **kwargs: Any) -> None:
        self.db = db
        super().__init__(**kwargs)

    @staticmethod
    def _draft_content(resources: list[dict[str, Any]]) -> str:
        latest = resources[-1]
        output = latest.get("output") if isinstance(latest, dict) else None
        markdown = output.get("markdown") if isinstance(output, dict) else None
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("Critic Adapter无法从最新Resource提取markdown")
        return markdown

    def execute(
        self, agent_input: CriticInput, state: AgentState
    ) -> QualityDecisionOutput:
        rag_context = agent_input.knowledge_state.get("rag_context", [])
        if not isinstance(rag_context, list):
            raise ValueError("knowledge_state.rag_context必须为列表")
        result = critic_agent.run_critic(
            self.db,
            draft_content=self._draft_content(agent_input.resources),
            rag_context=rag_context,
        )
        output = result["output"]
        passed = bool(output.get("passed"))
        decision = QualityDecision.PASS if passed else QualityDecision.REVISE
        score = float(output.get("validationScore", 0.0))
        rate = float(output.get("hallucinationRate", 1.0))
        issues = [str(item) for item in output.get("issues", [])]
        reason = (
            f"质量校验通过，评分{score:.4f}"
            if passed
            else f"质量校验未通过，评分{score:.4f}，需要修订"
        )
        return QualityDecisionOutput(
            decision=decision,
            passed=passed,
            validation_score=score,
            hallucination_rate=rate,
            issues=issues,
            reason=reason,
        )


# ── R1：planner / dialogue / feynman 三个业务 Agent 的适配 ─────────────────────


class PathPlanningInput(_StrictModel):
    """``planner_agent.plan_path`` 的入参（读画像/掌握度/岗位/知识树）。"""

    user_id: str
    target_job_id: str | None = None
    # True=理由经 LLMClient（POST generate）；False=强制确定性模板零网络（GET 实时重算）
    narrate: bool = True


class PathPlanningOutput(_StrictModel):
    """``plan_path`` 的返回：lessons（含 kpId/reason/resources）+ summary。"""

    lessons: list[dict[str, Any]]
    summary: str


class PathPlanningAgentAdapter(
    BaseAgent[PathPlanningInput, PathPlanningOutput]
):
    agent_name = "path_planning"
    description = "适配现有学习路径规划Agent，不改排序/先修闭包/板块裁剪等确定性算法"
    input_schema = PathPlanningInput
    output_schema = PathPlanningOutput
    capabilities = (
        "path_planning",
        "prereq_closure",
        "focus_board_selection",
        "resource_recommendation",
        "timeline_estimate",
    )
    tools: tuple[str, ...] = ()
    prompt_id = "planner"

    def __init__(self, db: Session, **kwargs: Any) -> None:
        self.db = db
        super().__init__(**kwargs)

    def execute(
        self, agent_input: PathPlanningInput, state: AgentState
    ) -> PathPlanningOutput:
        result = planner_agent.plan_path(
            self.db,
            user_id=agent_input.user_id,
            target_job_id=agent_input.target_job_id,
            narrate=agent_input.narrate,
        )
        return PathPlanningOutput.model_validate(result)


# dialogue_agent 的十个纯逻辑函数 → operation 白名单（1:1 委托，无业务搬运）。
DialogueOperation = Literal[
    "opening_question",
    "extract_subjective",
    "next_opening_key",
    "opening_reply",
    "quiz_interaction",
    "microtest_reply",
    "preference_interaction",
    "preference_reply",
    "classify_preference",
    "closing_reply",
]

_DIALOGUE_FUNCTIONS: dict[str, str] = {
    "opening_question": "opening_first_question",
    "extract_subjective": "extract_subjective",
    "next_opening_key": "next_opening_key",
    "opening_reply": "opening_reply",
    "quiz_interaction": "quiz_interaction",
    "microtest_reply": "microtest_reply",
    "preference_interaction": "preference_interaction",
    "preference_reply": "preference_reply",
    "classify_preference": "classify_preference",
    "closing_reply": "closing_reply",
}


class DialogueInput(_StrictModel):
    """状态机单入口：``operation`` 决定调哪个纯逻辑函数，``payload`` 为其关键字参数。"""

    operation: DialogueOperation
    payload: dict[str, Any] = Field(default_factory=dict)


class DialogueOutput(_StrictModel):
    """``result`` 类型随 operation 而异（str / list / dict），故用 Any 承载。"""

    operation: str
    result: Any


class DialogueAgentAdapter(BaseAgent[DialogueInput, DialogueOutput]):
    agent_name = "dialogue"
    description = "对话式学情诊断状态机的单入口适配；会话与DB编排仍在 services.profile_dialogue"
    input_schema = DialogueInput
    output_schema = DialogueOutput
    capabilities = (
        "subjective_extraction",
        "microtest_interaction",
        "preference_classification",
        "stage_transition",
    )
    tools: tuple[str, ...] = ()
    prompt_id = "dialogue"

    def execute(self, agent_input: DialogueInput, state: AgentState) -> DialogueOutput:
        fn = getattr(dialogue_agent, _DIALOGUE_FUNCTIONS[agent_input.operation])
        try:
            result = fn(**dict(agent_input.payload))
        except TypeError as exc:
            raise ValueError(
                f"dialogue operation「{agent_input.operation}」参数不匹配：{exc}"
            ) from exc
        return DialogueOutput(operation=agent_input.operation, result=result)


class FeynmanInput(_StrictModel):
    """``feynman_agent.respond`` 的入参（history 支撑「补讲—再评」迭代）。"""

    kp_id: str
    kp_name: str
    description: str = ""
    history: list[dict[str, str]] = Field(default_factory=list)
    explanation: str


class FeynmanOutput(_StrictModel):
    """``llm.feynman_eval`` 契约：{feedback, gaps, score, followups, complete}。

    ``score`` 用 float 以同时兼容 mock 的 int 与契约清洗后的 int（Pydantic lax 模式
    int → float），避免严格 int 校验在小数分上误判。
    """

    feedback: str
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    score: float
    followups: list[str] = Field(default_factory=list)
    complete: bool


class FeynmanAgentAdapter(BaseAgent[FeynmanInput, FeynmanOutput]):
    agent_name = "feynman"
    description = "适配现有费曼讲解评估Agent，不改变缺口定位与追问生成逻辑"
    input_schema = FeynmanInput
    output_schema = FeynmanOutput
    capabilities = (
        "explanation_evaluation",
        "gap_localization",
        "followup_questioning",
    )
    tools: tuple[str, ...] = ()
    prompt_id = "feynman"

    def execute(self, agent_input: FeynmanInput, state: AgentState) -> FeynmanOutput:
        result = feynman_agent.respond(
            kp_id=agent_input.kp_id,
            kp_name=agent_input.kp_name,
            description=agent_input.description,
            history=agent_input.history,
            explanation=agent_input.explanation,
        )
        return FeynmanOutput.model_validate(result)
