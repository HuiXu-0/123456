"""LLM 调用适配层（B2-a 落地 mock；B5-b 接入 deepseek 真实 provider）。

CLAUDE.md 工程纪律：所有生成类接口必须经本层调用，provider 可配
`mock / deepseek / qwen / anthropic`。
- **mock**：按接口契约返回确定性结构化假数据，无任何 API Key 即可跑通全链路
  （前端演示兜底，B5-b 后行为仍与 B2 逐字等价）；
- **deepseek**（B5-b）：经 app.core.llm_deepseek.chat（openai 兼容 SDK）真实生成，
  结构化输出做契约清洗（枚举校验 / level 截断 / sourceId 白名单 / JSON 容错解析），
  上游异常统一 LLMGenerationError → 路由映射 code 2001；
- qwen / anthropic：仍留占位，抛 NotImplementedError。

设计：
- 语义化方法（extract_profile / generate_narrative）而非裸 `complete()`，因为各生成
  接口需返回与接口文档逐字对齐的结构化数据；
- `complete()` 供 Agent 节点调用（B5-a 工作流）；真实模式只发渲染后 prompt；
- `get_llm()` 返回进程内单例，按 settings.llm_provider 选择实现。

字段口径依据《后端接口文档》4.1（ParsedProfile.skills / 枚举）、4.2（Narrative）、
2.4（雷达 6 维固定键名）、15.3（幻觉率口径，critic 真实实现在 agents.critic_agent）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from app.core import content_safety, generation_provenance, llm_transport
from app.core.config import settings
from app.core.llm_cornell import (
    CORNELL_TEMPLATES as _CORNELL_TEMPLATES,
    assemble_cornell as _assemble_cornell_payload,
    clean_cornell as _clean_cornell_payload,
    generate_mock_cornell as _generate_mock_cornell,
)
from app.core.llm_deepseek import LLMGenerationError  # 路由层从本模块导入（re-export）
from app.core.llm_diagram import (
    DIAGRAM_TEMPLATES as _DIAGRAM_TEMPLATES,
    generic_diagram as _generic_diagram,
)
from app.core.llm_document_mock import (
    answer_document_question as _mock_doc_answer,
    extract_key_sentences as _doc_key_sentences,
)
from app.core.llm_feynman import (
    FEYNMAN_BLOCKING as _FEYNMAN_BLOCKING,
    FEYNMAN_CONCEPTS as _FEYNMAN_CONCEPTS,
    SEVERITY_RANK as _SEVERITY_RANK,
    clean_feynman as _clean_feynman_payload,
    evaluate_mock as _mock_feynman_evaluation,
)
from app.core.llm_flashcards import (
    clean_flashcards as _clean_flashcards_payload,
    generate_mock_flashcards as _generate_mock_flashcards,
)
from app.core.llm_output import (
    clean_mermaid as _clean_mermaid,
    extract_json as _extract_json,
    strip_markdown_fence as _strip_md_fence,
)
from app.core.llm_portrait import (
    ABILITY_DIM_KEYS,
    PORTRAIT_DIMENSIONS,
    PORTRAIT_DIM_KINDS,
    PORTRAIT_KEYS as _PORTRAIT_KEYS,
    PORTRAIT_KINDS as _PORTRAIT_KINDS,
    PORTRAIT_LABELS as _PORTRAIT_LABELS,
    PREFERENCE_DIM_KEYS,
    PREFERENCE_LABELS as _PREFERENCE_LABELS,
    PREFERENCE_QUESTIONS,
    SUBJECTIVE_DIM_KEYS,
    extract_mock_portrait as _mock_extract_portrait,
    sanitize_portrait_updates as _sanitize_portrait_updates,
)
from app.core.llm_practice import audit_practice
from app.core.llm_reinforcement import (
    REINFORCE_BANK as _REINFORCE_BANK,
    clean_reinforcement as _clean_reinforcement_payload,
    generate_mock_card as _generate_mock_reinforce_card,
)
from app.core.llm_scoring import (
    character_bigrams as _char_bigrams,
    clamp_score as _clamp_score,
    credibility_score as _credibility_of,
    point_coverage as _point_coverage,
)
from app.core.llm_tutor_mock import (
    REMEDIAL_TYPES,
    build_remedial_suggestions as _build_remedial_suggestions,
    identify_problem as _mock_identify_problem,
    tutor_reply as _mock_tutor_reply,
)
from app.core.llm_video_script import (
    VIDEO_SCRIPT_NN as _VIDEO_SCRIPT_NN,
    clean_video_script as _clean_video_script_payload,
    generate_mock_video_script as _generate_mock_video_script,
    video_scene as _build_video_scene,
)
from app.core.llm_workflow_mock import (
    diagnose as _mock_workflow_diagnosis,
    generate as _mock_workflow_generation,
    review as _mock_workflow_critic,
    set_force_critic_low,
)

logger = logging.getLogger("app.core.llm")

__all__ = [
    "LLMClient",
    "LLMGenerationError",
    "audit_practice",
    "get_llm",
    "set_force_critic_low",
]

# 雷达 / 画像 6 维固定键名（接口文档 2.4，画像与岗位共用以便对标）
ABILITY_DIMENSIONS: list[str] = [
    "机器学习基础",
    "神经网络",
    "深度学习",
    "注意力机制",
    "Transformer",
    "大模型微调",
]

# mock 基线能力值（接口文档 4.1/4.4 示例：85/72/68/45/30/20）
_MOCK_BASELINE: list[int] = [85, 72, 68, 45, 30, 20]

# 讲义资源页难度档：入门|初级|中级|高级|精通（五档，与文档学习/路径难度档对齐）。
# 每档对应 lecture_content 的一个递进 depth（0–4），中级/精通与相邻档产出深度可区分。
LECTURE_DIFFICULTIES: list[str] = ["入门", "初级", "中级", "高级", "精通"]

# 学习路径难度阶梯（接口文档 2.3 Lesson.difficulty）：规划 Agent 按画像基础上下浮动
PATH_DIFFICULTIES: list[str] = ["入门", "初级", "中级", "高级", "精通"]


# mock 讲义的 RAG 来源（接口文档 8.2 sources；type∈教材|论文|文档|课程，confidence 0-1）。
# B5 接入真实 RAG 后由检索命中切片回填，此处为确定性占位。
_LECTURE_SOURCES: list[dict[str, Any]] = [
    {"title": "《深度学习》(花书) 相关章节", "type": "教材", "confidence": 0.92},
    {"title": "Stanford CS231n / CS224n 公开课讲义", "type": "课程", "confidence": 0.88},
    {"title": "领域权威综述与官方技术文档", "type": "文档", "confidence": 0.83},
]

# mock 幻觉率（接口文档 8.2 示例 0.021，前端显示「<5%」）。
# B5 替换为 15.3 逐句接地校验口径（未接地句数/总句数）；mock 阶段确定性返回。
_LECTURE_HALLUCINATION_RATE: float = 0.021


# 关键词 → 维度索引，用于在 mock 中「读到」材料文本时小幅抬升对应维度，
# 体现解析的可解释性；未命中则用基线值（不编造、不随机）。
# 接口文档 4.1 枚举（真实抽取的契约清洗白名单；非法值回落「其他」）
_EDUCATION_ENUM = ("本科", "硕士", "博士", "其他")
_MAJOR_ENUM = ("计算机科学", "电子信息", "人工智能", "软件工程", "数据科学", "其他")
_GOAL_ENUM = ("职业培训", "技能认证", "学术研究", "兴趣学习", "其他")

# ---- 苏格拉底辅导（接口文档 8.7 / 15.4，B7-a） ---------------------------------

# deepseek 真实模式 system prompt：核心约束「引导式提问、不直接给答案」
_TUTOR_SYSTEM = (
    "你是「{kp_name}」的苏格拉底式导学助手。规则：只用引导式提问启发学习者自己想通，"
    "绝对不直接给出最终答案或完整结论；每次回复聚焦一个思考点并以一个问题收尾；"
    "学习者答对时先简短肯定再追问下一层；简体中文，单次回复不超过 120 字。"
)

# Mock 苏格拉底回复与按需补救建议由 llm_tutor_mock 提供；真实模式 Prompt 仍由本层编排。
# ---- 外部资源·联网搜索聚合（接口文档 8.6 增量，C-fix 批3-bonus） ----------------
_AGG_TYPES: tuple[str, ...] = ("视频", "论文", "文档", "课程")


# 简答题型（C-fix 批2，9.1 扩展）：options 为空，correct_answer 存参考要点列表，
# explanation 存参考答案；判分经 LLMClient.score_short_answer（mock 确定性兜底）。
SHORT_ANSWER_TYPE = "short_answer"


# ---- 文档问答（「和文档对话」严格基于文档 + 溯源，mock 兜底） ----------------
_DOC_CHAT_SYSTEM = (
    "你是「文档问答」助手，只依据用户提供的《{source_title}》文档片段回答问题，目标是帮助用户"
    "「读懂这篇文档」。铁律：只用给定文档片段中的信息，**禁止**引入文档之外的知识或编造事实；"
    "文档片段里没有答案时，明确回答「文档中未提及该内容」。在引用具体内容处以 [n] 标注对应"
    "文档片段序号（n 从 1 起，对应给定片段顺序），便于溯源。简体中文，条理清晰、简洁作答。"
)




_KEYWORD_TO_DIM: dict[str, int] = {
    "机器学习": 0,
    "machine learning": 0,
    "神经网络": 1,
    "neural": 1,
    "深度学习": 2,
    "deep learning": 2,
    "注意力": 3,
    "attention": 3,
    "transformer": 4,
    "微调": 5,
    "finetune": 5,
    "fine-tune": 5,
    "lora": 5,
}


# ---- 学习流程：康奈尔线索 / 费曼讲解评估（接口文档第 18 章，C2） ----------------




def _tool_descriptor(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """兼容两种 tool 描述格式 → ``(name, parameters_json_schema)``。

    - **LangChain / OpenAI 格式** ``{"type":"function","function":{name,description,parameters}}``
      —— R4.3 起 ``ToolLoop.schemas()`` 经 ``langchain_adapter`` 产出的形态；
    - **ToolSpec.to_dict() 格式** ``{"name":..., "argsSchema":{...}}``
      —— 直接传 ToolSpec 字典时的形态。
    """
    if isinstance(spec, dict) and isinstance(spec.get("function"), dict):
        fn = spec["function"] or {}
        return str(fn.get("name") or ""), dict(fn.get("parameters") or {})
    payload = spec or {}
    return str(payload.get("name") or ""), dict(payload.get("argsSchema") or {})


def _mock_tool_arguments(spec: dict[str, Any], task: str) -> dict[str, Any]:
    """按工具 JSON Schema 生成**确定性**入参（mock 决策用）。

    - ``user_id`` 一律**跳过** —— 由 Tool Loop 以运行时 ``actor_id`` 注入，
      避免模型伪造身份（越权守卫仍在 ``AgentTool._authorize``）；
    - ``query`` 类字段用任务文本（非空、可复现）；
    - 其余必填字段按类型给最小合法值，保证能通过 ``args_schema`` 校验。
    """
    _name, schema = _tool_descriptor(spec)
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    arguments: dict[str, Any] = {}
    for field in required:
        if field == "user_id":
            continue
        prop = properties.get(field) or {}
        ftype = prop.get("type")
        if field in ("query", "prompt", "text", "explanation"):
            arguments[field] = (task or field)[:64]
        elif ftype == "string":
            arguments[field] = str(prop.get("default") or "")
        elif ftype == "integer":
            minimum = prop.get("minimum")
            arguments[field] = int(minimum) if minimum is not None else int(prop.get("default") or 1)
        elif ftype == "number":
            arguments[field] = float(prop.get("default") or 0.0)
        elif ftype == "boolean":
            arguments[field] = bool(prop.get("default", False))
        elif ftype == "array":
            arguments[field] = []
        elif ftype == "object":
            arguments[field] = {}
    return arguments


class LLMClient:
    """LLM 适配层。provider=mock 时确定性产出；其余 provider 为 B5 占位。"""

    def __init__(self, provider: str | None = None) -> None:
        self.provider = provider or settings.llm_provider

    # ---- provider 守卫 ----------------------------------------------------
    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    def _ensure_supported(self) -> None:
        if self.provider not in ("mock", "deepseek"):
            raise NotImplementedError(
                f"LLM provider '{self.provider}' 尚未实现："
                "当前支持 mock / deepseek；qwen / anthropic 为后续占位。"
            )

    # ---- 画像抽取（接口文档 4.1） -----------------------------------------
    @staticmethod
    def _keyword_skills(text: str, source: str) -> list[dict[str, Any]]:
        """确定性 6 维技能基线：命中关键词的维度小幅抬升（mock / 无材料兜底共用）。"""
        levels = list(_MOCK_BASELINE)
        lowered = (text or "").lower()
        for keyword, dim in _KEYWORD_TO_DIM.items():
            if keyword in lowered:
                levels[dim] = min(100, levels[dim] + 8)
        return [
            {"name": name, "level": level, "source": source}
            for name, level in zip(ABILITY_DIMENSIONS, levels)
        ]

    def parse_skills(self, text: str, source: str) -> list[dict[str, Any]]:
        """从材料文本抽取 6 维技能画像（接口文档 4.1 skills，mock 确定性口径）。

        以基线值为底，命中关键词的维度小幅抬升（上限 100），每项 source 取调用方
        传入的来源类型（resume|ocr|text|manual）。无文本（纯手动）时 source 应为
        manual，且仅给基线值不编造经历。真实模式请使用 extract_profile。
        """
        return self._keyword_skills(text, source)

    def extract_profile(self, text: str, source: str) -> dict[str, Any]:
        """抽取完整结构化画像（接口文档 4.1：education/major/goal/skills）。

        - mock 或**无材料文本**（manual，防幻觉约束：不得调用 LLM 编造经历）：
          确定性产出，与 B2 行为逐字等价；
        - deepseek：真实抽取 + 契约清洗（枚举白名单、level 截断 0-100、
          固定 6 维补齐、source 统一为调用方传入值）。
        """
        self._ensure_supported()
        stripped = (text or "").strip()
        if self.is_mock or not stripped:
            return {
                "education": "硕士",
                "major": "人工智能",
                "goal": "职业培训",
                "skills": self._keyword_skills(stripped, source),
            }
        return self._deepseek_extract_profile(stripped, source)

    def _deepseek_extract_profile(self, text: str, source: str) -> dict[str, Any]:
        system = (
            "你是学习者画像抽取专家。从材料文本中抽取结构化画像，仅输出 JSON："
            '{"education": "...", "major": "...", "goal": "...", '
            '"skills": [{"name": "...", "level": 0}]}。'
            f"education 取值 {list(_EDUCATION_ENUM)}；major 取值 {list(_MAJOR_ENUM)}；"
            f"goal 取值 {list(_GOAL_ENUM)}；"
            f"skills.name 必须取自固定 6 维 {ABILITY_DIMENSIONS}，level 为 0-100 整数，"
            "仅依据材料体现的能力给出，材料未体现的维度可省略；"
            "材料明确说明某维度零基础/未接触/从未学过时，该维度必须给出且 level 取 0-10"
            "（显式负证据不是「未体现」，省略会被默认值虚高）；"
            "禁止编造材料中不存在的经历。"
        )
        raw = llm_transport.chat(f"材料文本：\n{text}", system=system)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise LLMGenerationError("画像抽取输出无法解析为契约 JSON")

        def _pick(value: Any, enum: tuple[str, ...]) -> str:
            return value if value in enum else "其他"

        by_name: dict[str, int] = {}
        for s in data.get("skills") or []:
            if not isinstance(s, dict):
                continue
            name, level = s.get("name"), s.get("level")
            if name in ABILITY_DIMENSIONS and isinstance(level, (int, float)):
                by_name[name] = max(0, min(100, int(level)))  # 截断 0-100
        skills = [
            {"name": name, "level": by_name.get(name, baseline), "source": source}
            for name, baseline in zip(ABILITY_DIMENSIONS, _MOCK_BASELINE)
        ]
        return {
            "education": _pick(data.get("education"), _EDUCATION_ENUM),
            "major": _pick(data.get("major"), _MAJOR_ENUM),
            "goal": _pick(data.get("goal"), _GOAL_ENUM),
            "skills": skills,
        }

    def generate_narrative(
        self,
        draft: dict[str, Any],
        materials: list[dict[str, Any]],
        target_job: dict[str, Any] | None,
    ) -> list[list[dict[str, Any]]]:
        """生成两段式带来源标注的画像叙述（接口文档 4.2 paragraphs）。

        返回恰好两段，每段为 NarrativeSegment 数组：
        - 第一段：背景与优势（tone=key，sourceId 指向支撑材料）；
        - 第二段：与目标岗位差距（tone=weak，定位最弱维度）。
        调用方负责在「无材料」时不调用本方法（叙述应返回 null，防幻觉）。
        deepseek 模式：真实生成 + 契约清洗（恰好两段、sourceId 白名单、tone 校验）。
        """
        self._ensure_supported()
        if not self.is_mock:
            return self._deepseek_narrative(draft, materials, target_job)
        return self._mock_narrative(draft, materials, target_job)

    def _mock_narrative(
        self,
        draft: dict[str, Any],
        materials: list[dict[str, Any]],
        target_job: dict[str, Any] | None,
    ) -> list[list[dict[str, Any]]]:
        """B2 确定性两段叙述（mock 模式逐字等价保留）。"""
        skills: list[dict[str, Any]] = draft.get("skills") or []
        major = (draft.get("major") or {}).get("value") or "人工智能"
        # 首个材料作为关键句的来源锚点
        source_id = materials[0]["id"] if materials else None

        # 优势维度（按 level；薄弱维度在 _gap_paragraph 内定位）
        strongest = (
            max(skills, key=lambda s: s.get("level", 0))
            if skills
            else {"name": ABILITY_DIMENSIONS[0]}
        )

        para1 = [
            {"text": "该学习者具备 ", "sourceId": None},
            {"text": major, "tone": "key", "sourceId": source_id},
            {"text": " 专业背景，", "sourceId": None},
            {"text": f"{strongest['name']}能力突出", "tone": "key", "sourceId": source_id},
            {"text": "。"},
        ]

        para2 = self._gap_paragraph(draft, target_job)
        return [para1, para2]

    @staticmethod
    def _gap_paragraph(
        draft: dict[str, Any], target_job: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """确定性「差距段」（mock 第二段；真实模式段数不足时的兜底段）。"""
        skills: list[dict[str, Any]] = draft.get("skills") or []
        weakest = (
            min(skills, key=lambda s: s.get("level", 0))
            if skills
            else {"name": ABILITY_DIMENSIONS[-1]}
        )
        job_name = (target_job or {}).get("name")
        lead = f"与目标岗位「{job_name}」相比，" if job_name else "与目标岗位要求相比，"
        return [
            {"text": lead, "sourceId": None},
            {"text": f"{weakest['name']}能力偏弱", "tone": "weak"},
            {"text": "，建议作为后续学习路径的优先强化方向。"},
        ]

    def _deepseek_narrative(
        self,
        draft: dict[str, Any],
        materials: list[dict[str, Any]],
        target_job: dict[str, Any] | None,
    ) -> list[list[dict[str, Any]]]:
        """真实两段叙述 + 契约清洗（接口文档 4.2）。

        sourceId 标注规则与 4.2 契约一致：只能取材料列表中的 id（白名单），
        模型给出列表外 id 一律置 null（防幻觉引用）；tone 仅保留 key|weak。
        """
        material_ids = {m["id"] for m in materials}
        system = (
            "你是学习画像叙述专家。基于给定结构化画像草稿与材料清单，生成恰好两段叙述，"
            '仅输出 JSON：{"paragraphs": [[{"text": "...", "tone": "key", '
            '"sourceId": "m1"}], [{"text": "..."}]]}。'
            "规则：第一段讲学习者背景与优势，关键事实片段 tone=key 并把 sourceId 标注为"
            "支撑该事实的材料 id；第二段讲与目标岗位的差距，薄弱点片段 tone=weak；"
            "sourceId 只能取材料清单中的 id，无材料支撑的片段 sourceId 置 null；"
            "tone 只能取 key 或 weak，普通叙述片段省略 tone；"
            "禁止编造材料中不存在的经历。"
        )
        payload = {
            "draft": draft,
            "materials": materials,
            "targetJob": target_job,
        }
        raw = llm_transport.chat(
            json.dumps(payload, ensure_ascii=False), system=system
        )
        data = _extract_json(raw)
        if not isinstance(data, dict) or not isinstance(data.get("paragraphs"), list):
            raise LLMGenerationError("叙述输出无法解析为契约 JSON")

        paragraphs: list[list[dict[str, Any]]] = []
        for para in data["paragraphs"][:2]:
            if not isinstance(para, list):
                continue
            segments: list[dict[str, Any]] = []
            for seg in para:
                if not isinstance(seg, dict) or not seg.get("text"):
                    continue
                cleaned: dict[str, Any] = {"text": str(seg["text"])}
                if seg.get("tone") in ("key", "weak"):
                    cleaned["tone"] = seg["tone"]
                sid = seg.get("sourceId")
                cleaned["sourceId"] = sid if sid in material_ids else None
                segments.append(cleaned)
            if segments:
                paragraphs.append(segments)
        if not paragraphs:
            raise LLMGenerationError("叙述输出不含有效段落")
        while len(paragraphs) < 2:  # 恰好两段（4.2 契约）：不足时补确定性差距段
            paragraphs.append(self._gap_paragraph(draft, target_job))
        return paragraphs

    def generate_lecture(
        self,
        kp_id: str,
        kp_name: str,
        difficulty: str,
        description: str = "",
        tier: str | None = None,
    ) -> dict[str, Any]:
        """生成自适应讲义（接口文档 8.2）。返回 markdown + sources + hallucinationRate。

        mock 口径：按难度档 + 画像能力档 tier（advanced/beginner/basic/None）确定性产出
        递进式 Markdown（概念→原理含 LaTeX 公式→例子→代码→误区→小结）。tier 由调用方
        据请求用户画像派生（services.resource），使同一知识点对不同用户产出深度不同的讲义；
        tier=None（无画像信号）→ 按难度基线。sources/hallucinationRate 为占位常量。
        真实模式讲义不走本方法——经 workflows.run_learning_workflow 生成。
        """
        if not self.is_mock:
            raise LLMGenerationError(
                "真实模式讲义应经 run_learning_workflow 生成，不应调用 generate_lecture"
            )
        return {
            "markdown": self._lecture_markdown(kp_name, difficulty, description, tier),
            "sources": [dict(s) for s in _LECTURE_SOURCES],
            "hallucinationRate": _LECTURE_HALLUCINATION_RATE,
        }

    @staticmethod
    def _lecture_markdown(
        name: str, difficulty: str, description: str, tier: str | None = None
    ) -> str:
        """生成递进式讲义 Markdown（确定性 mock / 真实回落共用）。

        结构：概念引入→核心原理(含 LaTeX 公式)→具体例子→代码示例→常见误区→小结。
        - difficulty 决定基线深度；tier（画像能力档 advanced/beginner/basic）个性化主控，
          使「同一知识点、同一难度」对能力强 / 零基础两位用户产出深度不同的讲义；
        - tier=None（直出 / 无画像信号）→ 按难度基线，保证直出与工作流产物在同 depth 逐字一致。
        合成逻辑见 app.core.lecture_content.compose_lecture（纯函数，便于扩充知识原子）。
        """
        from app.core import lecture_content

        return lecture_content.compose_lecture(name, difficulty, description, tier)

    # ---- 简答题 AI 评分（接口文档 9.3，C-fix 批2） -----------------------------
    def score_short_answer(
        self,
        question_text: str,
        reference_points: list[str],
        reference_answer: str,
        student_answer: str,
    ) -> dict[str, Any]:
        """简答题对照参考要点评分（0-100 + 简短点评）。

        - mock 或空作答：确定性评分（参考要点字符二元组覆盖度），无任何 Key 不崩；
        - deepseek：真实评分 + 契约清洗（score 截断 0-100 整数、comment 非空）；
          上游异常 → 回落确定性评分（演示兜底，不向路由抛 2001）。
        """
        self._ensure_supported()
        answer = (student_answer or "").strip()
        points = [p for p in (reference_points or []) if isinstance(p, str) and p.strip()]
        if self.is_mock or not answer:
            return self._mock_score_short_answer(answer, points)
        try:
            return self._deepseek_score_short_answer(
                question_text, points, reference_answer, answer
            )
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("简答评分真实生成失败，回落确定性评分：%s", exc)
            return self._mock_score_short_answer(answer, points)

    @staticmethod
    def _mock_score_short_answer(answer: str, points: list[str]) -> dict[str, Any]:
        """确定性简答评分：参考要点字符二元组覆盖度 → 0-100 + 覆盖/待补点评。"""
        answer = (answer or "").strip()
        if not answer:
            tail = "参考要点：" + "；".join(points) if points else ""
            return {"score": 0, "comment": ("未作答。" + tail).strip()}
        if not points:
            score = 70 if len(answer) >= 16 else 50 if len(answer) >= 6 else 30
            return {"score": score, "comment": "已作答，按作答完整度给分。"}
        per = [(_point_coverage(p, answer), p) for p in points]
        covered = [p for c, p in per if c >= 0.34]
        missing = [p for c, p in per if c < 0.34]
        avg = sum(c for c, _ in per) / len(per)
        # 覆盖各要点二元组的约一半即满分（鼓励复述关键概念，而非逐字照抄参考答案）
        score = max(0, min(100, round(min(1.0, avg / 0.5) * 100)))
        parts: list[str] = []
        if covered:
            parts.append("已覆盖：" + "、".join(covered))
        if missing:
            parts.append("可补充：" + "、".join(missing))
        return {"score": score, "comment": ("；".join(parts) + "。") if parts else "已完成评分。"}

    def _deepseek_score_short_answer(
        self,
        question_text: str,
        points: list[str],
        reference_answer: str,
        answer: str,
    ) -> dict[str, Any]:
        """真实简答评分 + 契约清洗（score 0-100 整数、comment 非空）。"""
        system = (
            "你是严谨的简答题阅卷老师。对照参考要点与参考答案给学生作答打分，"
            '仅输出 JSON：{"score": 0, "comment": "..."}。'
            "score 为 0-100 整数，按要点覆盖程度与准确性评分；comment 为一句中文点评，"
            "指出答到的要点与缺漏，不超过 60 字；禁止编造学生未写的内容。"
        )
        payload = {
            "question": question_text,
            "referencePoints": points,
            "referenceAnswer": reference_answer,
            "studentAnswer": answer,
        }
        raw = llm_transport.chat(json.dumps(payload, ensure_ascii=False), system=system)
        data = _extract_json(raw)
        if not isinstance(data, dict) or "score" not in data:
            raise LLMGenerationError("简答评分输出无法解析为契约 JSON")
        try:
            score = max(0, min(100, int(data["score"])))
        except (TypeError, ValueError) as exc:
            raise LLMGenerationError("简答评分 score 非法") from exc
        comment = str(data.get("comment") or "").strip() or "已完成评分。"
        return {"score": score, "comment": comment}

    # ---- 学习过程评估叙述（接口文档 12.2，C-fix 批3） --------------------------
    def evaluate_learning(
        self, signals: dict[str, Any], metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """学习评估叙述：综述 + 学习方法建议 + 动态调整建议。

        - 动态调整（adjustment）**始终确定性派生**（保证 nextKpId 合法、不被幻觉）；
        - 综述/方法建议：mock 据信号模板 / deepseek 真实生成 + 契约清洗，上游异常回落 mock。
        无任何 Key 也能跑通（mock 兜底）。
        """
        self._ensure_supported()
        adjustment = self._eval_adjustment(signals, metrics)
        if self.is_mock:
            narrative = self._mock_eval_narrative(signals, metrics)
        else:
            try:
                narrative = self._deepseek_eval_narrative(signals, metrics)
            except LLMGenerationError as exc:
                generation_provenance.mark_degraded()
                logger.warning("学习评估真实生成失败，回落确定性叙述：%s", exc)
                narrative = self._mock_eval_narrative(signals, metrics)
        return {
            "summary": narrative["summary"],
            "suggestions": narrative["suggestions"],
            "adjustment": adjustment,
        }

    @staticmethod
    def _eval_adjustment(signals: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
        """确定性动态调整建议：下一步知识点（薄弱点优先）+ 难度建议 + 行动。"""
        dims = {d["key"]: d["score"] for d in metrics.get("dimensions", [])}
        weak = metrics.get("weakPoints") or []
        attempts = signals.get("attemptCount", 0)
        trend = metrics.get("trend", "stable")
        if weak:
            nxt = weak[0]
            next_id, next_name = nxt.get("kpId"), nxt.get("name")
            action = f"下一步优先攻克「{next_name}」：先看讲义 + 图解，再做阶段测试。"
        else:
            next_id = next_name = None
            action = "核心知识点已全部通过，可转入复习巩固或挑战更高难度内容。"
        qp = dims.get("quiz_performance", 0)
        mp = dims.get("mastery_progress", 0)
        if trend == "declining" or (attempts and qp < 60):
            difficulty_advice = "建议将讲义难度下调到「入门」，先夯实基础概念。"
        elif mp >= 80 and qp >= 80:
            difficulty_advice = "建议将讲义难度上调到「高级」，深入数学形式化与工程细节。"
        else:
            difficulty_advice = "建议维持「初级」难度，稳步推进、边学边测。"
        return {
            "nextKpId": next_id,
            "nextKpName": next_name,
            "difficultyAdvice": difficulty_advice,
            "action": action,
        }

    @staticmethod
    def _mock_eval_narrative(
        signals: dict[str, Any], metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """确定性评估综述 + 方法建议（mock / deepseek 兜底）。"""
        trend_label = {"improving": "稳步上升", "declining": "有所下滑", "stable": "基本平稳"}
        dims = {d["key"]: d["score"] for d in metrics.get("dimensions", [])}
        mastered = signals["masteredCount"]
        total = signals["totalCore"]
        attempts = signals["attemptCount"]
        weak = metrics.get("weakPoints") or []
        if attempts:
            summary = (
                f"你已掌握 {mastered}/{total} 个核心知识点，近 {attempts} 次测验平均最佳 "
                f"{signals['avgBestScore']} 分，分数趋势{trend_label[metrics['trend']]}，"
                f"整体处于「{metrics['level']}」阶段。"
            )
        else:
            summary = (
                f"你已掌握 {mastered}/{total} 个核心知识点，尚无测验记录，整体处于"
                f"「{metrics['level']}」阶段，建议尽快做一次阶段测试以校准学情。"
            )
        suggestions: list[str] = []
        if weak:
            names = "、".join(w["name"] for w in weak)
            suggestions.append(f"优先复习薄弱点：{names}，配合讲义/图解后重做阶段测试巩固。")
        if metrics["trend"] == "declining":
            suggestions.append("近期测验分数下滑，建议放慢节奏、回看讲义并用费曼讲解自检。")
        elif metrics["trend"] == "improving":
            suggestions.append("学习状态向好，保持当前节奏，可适当增加练习难度。")
        if dims.get("engagement", 0) < 50:
            suggestions.append("学习投入偏低，建议多用康奈尔笔记与费曼讲解加深理解。")
        if dims.get("mastery_progress", 0) >= 80:
            suggestions.append("基础掌握扎实，可尝试上调讲义难度到「高级」拓展深度。")
        if not suggestions:
            suggestions.append("继续按学习路径推进，保持测验与笔记的规律使用。")
        return {"summary": summary, "suggestions": suggestions[:4]}

    def _deepseek_eval_narrative(
        self, signals: dict[str, Any], metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """真实评估综述 + 方法建议 + 契约清洗（summary 非空、suggestions 为非空 str 列表）。"""
        system = (
            "你是学习数据分析师。基于给定的学习行为信号与多维指标，给出一句中文学习综述与"
            '2-4 条具体可执行的学习方法建议，仅输出 JSON：{"summary": "...", '
            '"suggestions": ["...", "..."]}。综述不超过 80 字；建议聚焦薄弱点复习、'
            "节奏与难度调整、笔记/费曼等方法；禁止编造未提供的数据。"
        )
        payload = {"signals": signals, "metrics": metrics}
        raw = llm_transport.chat(json.dumps(payload, ensure_ascii=False), system=system)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise LLMGenerationError("学习评估输出无法解析为契约 JSON")
        summary = str(data.get("summary") or "").strip()
        suggestions = [
            str(s).strip()
            for s in (data.get("suggestions") or [])
            if isinstance(s, str) and str(s).strip()
        ]
        if not summary or not suggestions:
            raise LLMGenerationError("学习评估输出缺少 summary/suggestions")
        return {"summary": summary, "suggestions": suggestions[:4]}

    # ---- 视频讲解分镜脚本（接口文档 8.3，画面/旁白随知识点动态生成） ----------
    def generate_video_script(
        self, kp_id: str, kp_name: str, difficulty: str, description: str = ""
    ) -> dict[str, Any]:
        """生成讲解视频分镜脚本（接口文档 8.3 scenes）。

        返回 {title, scenes:[{title, points:[str], narration}]}（3-5 个场景）。
        - mock：确定性、与主题相关的占位脚本——nn 用与前端原 5 场景逐字对齐的精写
          内容（不回归），其余知识点按相同结构参数化生成，内容紧扣 kpName，
          **绝不固定为神经网络**；
        - deepseek：真实生成 + 契约清洗（场景数截断 3-5、每场景要点 1-4 条、
          标题/旁白非空）；解析失败或上游异常时**回落确定性主题脚本**，
          保证视频始终可渲染（演示兜底，不向路由抛 2001）。
        """
        self._ensure_supported()
        if self.is_mock:
            return self._mock_video_script(kp_id, kp_name, difficulty, description)
        try:
            return self._deepseek_video_script(kp_name, difficulty, description)
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("视频分镜脚本真实生成失败，回落主题占位脚本：%s", exc)
            return self._mock_video_script(kp_id, kp_name, difficulty, description)

    @staticmethod
    def _video_scene(title: str, points: list[str], narration: str) -> dict[str, Any]:
        return _build_video_scene(title, points, narration)

    def _mock_video_script(
        self, kp_id: str, kp_name: str, difficulty: str, description: str
    ) -> dict[str, Any]:
        """确定性主题分镜脚本（mock / deepseek 兜底共用）。"""
        return _generate_mock_video_script(kp_id, kp_name, difficulty, description)

    def _deepseek_video_script(
        self, kp_name: str, difficulty: str, description: str
    ) -> dict[str, Any]:
        """真实分镜脚本生成 + 契约清洗（接口文档 8.3）。"""
        system = (
            "你是领域知识讲解视频的分镜脚本编剧。根据给定知识点生成用于讲解视频的"
            "结构化分镜脚本，仅输出 JSON："
            '{"title":"视频标题","scenes":[{"title":"场景标题",'
            '"points":["要点1","要点2"],"narration":"该场景旁白"}]}。'
            "要求：scenes 为 3-5 个场景，按「导入 → 核心概念 → 工作机制或示例 → "
            "方法对比或要点 → 小结」组织；每个场景 points 为 2-4 条精炼要点"
            "（每条不超过 20 字），narration 为 1-2 句口语化旁白（不超过 60 字）；"
            "所有内容必须紧扣该知识点主题，禁止跑题到其它领域；只输出 JSON，不要额外说明。"
        )
        prompt = f"知识点：{kp_name}\n难度档：{difficulty}\n知识点说明：{description or kp_name}"
        raw = llm_transport.chat(prompt, system=system)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise LLMGenerationError("视频分镜脚本输出无法解析为契约 JSON")
        return self._clean_video_script(data, kp_name)

    @staticmethod
    def _clean_video_script(data: dict[str, Any], kp_name: str) -> dict[str, Any]:
        """分镜脚本契约清洗：场景数 3-5、每场景要点 1-4 条、标题/旁白非空。"""
        return _clean_video_script_payload(data, kp_name)

    # ---- Mermaid 知识图解（接口文档 8.5，与讲义/视频同口径经 LLMClient 生成） ----
    def generate_diagram(
        self, kp_id: str, kp_name: str, description: str = ""
    ) -> dict[str, Any]:
        """生成 Mermaid 知识图解（接口文档 8.5）。返回 {mermaid}。

        - mock：确定性主题流程图——已收录知识点用精写模板（nn 与前端逐字一致），
          未收录知识点按主题参数化生成，恒以 `flowchart` 开头、紧扣 kpName；
        - deepseek：真实生成 mermaid + 契约清洗（首行须 flowchart/graph）；解析失败
          或上游异常时**回落确定性主题模板**，保证图解始终可渲染（不向路由抛 2001）。
        """
        self._ensure_supported()
        # GEN 板块（重点亮点·多图示）：精写论文级模板**优先于真实生成**——
        # 该板块图解质量为验收硬指标，确定性模板保证教材级标准结构（不抽卡）；
        # 其余板块维持原行为（真实生成优先，失败回落模板），不动已验收链路。
        if kp_id.startswith("GEN-") and kp_id in _DIAGRAM_TEMPLATES:
            return {"mermaid": _DIAGRAM_TEMPLATES[kp_id]}
        if self.is_mock:
            return {"mermaid": self._mock_diagram(kp_id, kp_name, description)}
        try:
            return {"mermaid": self._deepseek_diagram(kp_name, description)}
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("知识图解真实生成失败，回落主题占位图：%s", exc)
            return {"mermaid": self._mock_diagram(kp_id, kp_name, description)}

    @staticmethod
    def _mock_diagram(kp_id: str, kp_name: str, description: str) -> str:
        """确定性主题知识图解（mock / deepseek 兜底共用）。

        已收录知识点用精写模板（图型按内容各异）；未收录知识点按 description 动态合成
        （不同知识点产出不同图、图型按内容自选），见 _generic_diagram。
        """
        tpl = _DIAGRAM_TEMPLATES.get(kp_id)
        if tpl:
            return tpl
        return _generic_diagram(kp_name, description)

    def _deepseek_diagram(self, kp_name: str, description: str) -> str:
        """真实知识图解生成 + 契约清洗（接口文档 8.5）。

        提示要求按知识点内容结构**自选最贴切的图型**（流程/管线→flowchart；
        分类/谱系→mindmap 或 graph TD；模块结构→带 subgraph 的 flowchart），避免千篇一律。
        """
        system = (
            "你是深度学习/机器学习领域的知识图解专家。根据给定知识点生成一张**论文级标准示意图**"
            "（即该知识点在教材/论文里那类规范的架构图或流程图），用 Mermaid 画。"
            "只输出 Mermaid 源码本身，不要任何解释文字、不要 ``` 围栏。要求：\n"
            "1) 图必须还原该知识点的**真实标准结构**，例如：神经网络→输入层→隐藏层→输出层 + 前向/反向；"
            "CNN→卷积→ReLU→池化→全连接→Softmax；Transformer→嵌入+位置编码→多头自注意力→前馈→输出；"
            "梯度下降/训练→前向→损失→反向→参数更新的迭代回路。务必贴合本知识点的标准结构，不要臆造。\n"
            "2) 按内容自选最贴切的图型：流程/计算管线用 `flowchart LR` 或 `flowchart TD`；"
            "分类/谱系/对比用 `mindmap` 或 `graph TD` 层次图；含多模块的结构用带 `subgraph` 的 flowchart。\n"
            "3) 第一行必须是所选图型的合法声明（如 `flowchart TD` / `mindmap` / `graph TD`）。\n"
            "4) 6-14 个节点，体现该知识点的核心机制或构成；节点文字简短（不超过 12 字）"
            "且严格限定在本知识点的学术含义内——**禁止跑题到其它领域**（如 Transformer 不得画电力变压器、"
            "CNN 不得画新闻台、微调不得画乐器调音）。\n"
            "5) 只输出 Mermaid 源码。"
        )
        prompt = f"知识点：{kp_name}\n知识点说明：{description or kp_name}"
        raw = llm_transport.chat(prompt, system=system)
        return _clean_mermaid(raw)

    # ---- 文档学习：闪卡 + 文档练习题（平行链路，复用 LLMClient mock/real 双模 + 内容安全） ----
    def generate_flashcards(
        self,
        source_title: str,
        contexts: list[str],
        *,
        count: int = 8,
    ) -> dict[str, Any]:
        """从文档内容抽「正面问题 / 背面答案」闪卡集（接口文档 20.7）。返回 {cards:[{front,back}]}。

        contexts：该文档检索命中的切片内容（知识源，防幻觉——只据文档产出，不引入外部知识）。
        - mock：确定性——按文档要点句逐条转「问/答」卡，无随机、无网络；
        - deepseek：真实生成 + 契约清洗；解析失败回落确定性 mock（闪卡始终可用）。
        """
        self._ensure_supported()
        if self.is_mock:
            return {"cards": self._mock_flashcards(source_title, contexts, count)}
        try:
            return {"cards": self._deepseek_flashcards(source_title, contexts, count)}
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("闪卡真实生成失败，回落确定性 mock：%s", exc)
            return {"cards": self._mock_flashcards(source_title, contexts, count)}

    @staticmethod
    def _mock_flashcards(
        source_title: str, contexts: list[str], count: int
    ) -> list[dict[str, str]]:
        """确定性闪卡：取文档要点句，正面设问、背面即该句原文（内容严格来自文档）。"""
        return _generate_mock_flashcards(source_title, contexts, count)

    def _deepseek_flashcards(
        self, source_title: str, contexts: list[str], count: int
    ) -> list[dict[str, str]]:
        system = (
            "你是学习卡片设计专家。仅依据给定的文档片段，抽取要点做成正/背面记忆闪卡。"
            "严格只用文档片段中的信息，禁止编造文档之外的事实。"
            f'仅输出 JSON：{{"cards": [{{"front": "正面问题", "back": "背面答案"}}]}}，'
            f"卡片数不超过 {count} 张，front 是一个针对要点的简短问题，back 是简明答案。"
        )
        joined = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts) if c)[:6000]
        prompt = f"文档标题：{source_title}\n文档片段：\n{joined or source_title}"
        raw = llm_transport.chat(prompt, system=system)
        return _clean_flashcards_payload(raw, count)

    # ---- 文档学习：文档概览（NotebookLM 式速读，复用双模 + 内容安全 + 防幻觉） ----
    def generate_overview(self, source_title: str, contexts: list[str]) -> dict[str, Any]:
        """文档概览：这篇文档是什么、讲了什么、核心结构概况、关键点。

        返回 {summary, about, structure, keyPoints:[...]}。contexts 为该文档检索命中的
        切片（知识源，防幻觉——只据文档产出，不引入外部知识）。
        - mock：确定性——按文档要点句合成概览，无随机、无网络；
        - deepseek：真实生成 + 契约清洗；解析失败回落确定性 mock（概览始终可用）。
        """
        self._ensure_supported()
        if self.is_mock:
            return self._mock_overview(source_title, contexts)
        try:
            return self._deepseek_overview(source_title, contexts)
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("文档概览真实生成失败，回落确定性 mock：%s", exc)
            return self._mock_overview(source_title, contexts)

    @staticmethod
    def _mock_overview(source_title: str, contexts: list[str]) -> dict[str, Any]:
        """确定性文档概览：全部取材自文档要点句（严格来自文档、不臆造）。"""
        sentences = _doc_key_sentences(contexts, max_n=8)
        if not sentences:
            return {
                "summary": f"《{source_title}》暂未解析到可用于生成概览的正文内容。",
                "about": "请确认文档为可抽取文本的格式（PDF / TXT / Markdown / DOCX）后重试。",
                "structure": "—",
                "keyPoints": [],
            }
        topic = sentences[0][:24].rstrip("，,。.；;：: ")
        return {
            "summary": f"《{source_title}》是一篇围绕「{topic}」展开的资料。",
            "about": "".join(sentences[:2])[:180],
            "structure": (
                f"全文自「{sentences[0][:14]}」切入，逐步展开到「{sentences[-1][:14]}」，"
                f"共梳理约 {len(sentences)} 个要点。"
            ),
            "keyPoints": sentences[:5],
        }

    def _deepseek_overview(self, source_title: str, contexts: list[str]) -> dict[str, Any]:
        system = (
            "你是资料速读专家。仅依据给定文档片段，产出一份「文档概览」，帮助读者快速判断"
            "这篇文档是什么、讲了什么、核心内容/结构概况与关键点。"
            "严格只用文档片段中的信息，禁止编造文档之外的事实。"
            '仅输出 JSON：{"summary":"一句话这篇文档是什么","about":"讲了什么（2-3句）",'
            '"structure":"核心内容/结构概况","keyPoints":["关键点1","关键点2"]}。'
        )
        joined = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts) if c)[:6000]
        prompt = f"文档标题：{source_title}\n文档片段：\n{joined or source_title}"
        data = _extract_json(llm_transport.chat(prompt, system=system))
        if not isinstance(data, dict) or not str(data.get("summary") or "").strip():
            raise LLMGenerationError("文档概览输出无法解析为契约 JSON")
        kp = data.get("keyPoints")
        key_points = (
            [str(x).strip() for x in kp if str(x).strip()] if isinstance(kp, list) else []
        )
        return {
            "summary": str(data.get("summary") or "").strip(),
            "about": str(data.get("about") or "").strip(),
            "structure": str(data.get("structure") or "").strip(),
            "keyPoints": key_points[:8],
        }

    def generate_doc_quiz(
        self,
        source_title: str,
        contexts: list[str],
        *,
        count: int = 5,
    ) -> dict[str, Any]:
        """从文档内容出练习题（接口文档 20.6）。返回 {questions:[QuizQuestion 契约结构]}。

        - mock：确定性——按文档要点句出判断题（true/false，答案自洽），内容来自文档；
        - deepseek：真实生成 + audit_practice 自洽审核；不自洽/解析失败回落确定性 mock。
        """
        self._ensure_supported()
        if self.is_mock:
            return {"questions": self._mock_doc_quiz(source_title, contexts, count)}
        try:
            return {"questions": self._deepseek_doc_quiz(source_title, contexts, count)}
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("文档练习题真实生成失败，回落确定性 mock：%s", exc)
            return {"questions": self._mock_doc_quiz(source_title, contexts, count)}

    @staticmethod
    def _mock_doc_quiz(
        source_title: str, contexts: list[str], count: int
    ) -> list[dict[str, Any]]:
        """确定性判断题：取文档要点句，命题「以下说法是否符合文档」，答案恒 true（内容来自文档）。"""
        sentences = _doc_key_sentences(contexts, max_n=count)
        questions: list[dict[str, Any]] = []
        for i, sent in enumerate(sentences, start=1):
            questions.append(
                {
                    "question_id": f"docq_{i}",
                    "question_type": "boolean",
                    "question_text": f"根据《{source_title}》，以下说法是否正确：{sent}",
                    "options": [
                        {"option_id": "true", "option_text": "正确"},
                        {"option_id": "false", "option_text": "错误"},
                    ],
                    "correct_answer": "true",
                    "explanation": f"该表述取自文档原文要点：{sent}",
                }
            )
        return questions

    def _deepseek_doc_quiz(
        self, source_title: str, contexts: list[str], count: int
    ) -> list[dict[str, Any]]:
        system = (
            "你是命题专家。仅依据给定文档片段出练习题，严格只考文档中出现的知识，禁止编造。"
            f'仅输出 JSON：{{"questions": [{{"question_id": "docq_1", '
            '"question_type": "single|multiple|boolean", "question_text": "...", '
            '"options": [{"option_id": "a", "option_text": "..."}], '
            '"correct_answer": "option_id（multiple 为数组；boolean 用 true/false）", '
            '"explanation": "..."}]}。'
            f"题数不超过 {count}；correct_answer 必须取自 options 的 option_id；explanation 非空。"
        )
        joined = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts) if c)[:6000]
        prompt = f"文档标题：{source_title}\n文档片段：\n{joined or source_title}"
        data = _extract_json(llm_transport.chat(prompt, system=system))
        raw = data.get("questions") if isinstance(data, dict) else None
        questions: list[dict[str, Any]] = []
        for idx, q in enumerate(raw or [], start=1):
            if not isinstance(q, dict):
                continue
            options = [
                {"option_id": str(o.get("option_id")), "option_text": str(o.get("option_text") or "")}
                for o in (q.get("options") or [])
                if isinstance(o, dict) and o.get("option_id")
            ]
            cleaned = {
                "question_id": str(q.get("question_id") or f"docq_{idx}"),
                "question_type": q.get("question_type"),
                "question_text": str(q.get("question_text") or ""),
                "options": options,
                "correct_answer": q.get("correct_answer"),
                "explanation": str(q.get("explanation") or ""),
            }
            if not audit_practice(cleaned):  # 仅收自洽题（防幻觉/防错题）
                questions.append(cleaned)
        if not questions:
            raise LLMGenerationError("文档练习题输出无自洽题（审核未通过）")
        return questions[:count]

    # ---- 错题强化（接口文档 9.2，B6） --------------------------------------
    def generate_reinforcement(
        self, kp_name: str, wrong_questions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """错题 → 薄弱点定位 + recap + 一道针对性练习（接口文档 9.2）。

        wrong_questions：答错题的 QuizQuestion 契约结构（2.5）列表。
        - mock：确定性产出（nn 三题与前端 WeakPointReinforce 演示内容对齐，
          其余题走确定性变式兜底），无随机、无网络；
        - deepseek：真实生成 + critic 自洽审核（correct_answer 必须在 options 中、
          explanation 非空），不自洽时带审核意见重试一次，仍失败 → LLMGenerationError。
        """
        self._ensure_supported()
        if self.is_mock:
            return [self._mock_reinforce_card(q) for q in wrong_questions]
        return self._deepseek_reinforcement(kp_name, wrong_questions)

    @staticmethod
    def _mock_reinforce_card(question: dict[str, Any]) -> dict[str, Any]:
        """单题确定性强化卡。命中预置库用精写内容，否则确定性变式兜底。"""
        return _generate_mock_reinforce_card(question)

    def _deepseek_reinforcement(
        self, kp_name: str, wrong_questions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """真实强化生成 + critic 答案自洽审核（不自洽带反馈重试一次）。"""
        system = (
            "你是错题强化教练。针对每道答错的题，定位薄弱知识点并生成强化内容，"
            '仅输出 JSON：{"items": [{"questionId": "原题id", "point": "薄弱点短语", '
            '"recap": "针对性回顾讲解", "practice": {"question_id": "原题id-r", '
            '"question_type": "single|multiple|boolean", "question_text": "...", '
            '"options": [{"option_id": "a", "option_text": "..."}], '
            '"correct_answer": "option_id（multiple 为 option_id 数组）", '
            '"explanation": "..."}}]}。'
            "practice 必须是与原题同考点但不同问法的新题；correct_answer 必须取自 "
            "options 的 option_id；explanation 必须非空；items 数量与输入错题数一致。"
        )
        payload = {"knowledgePoint": kp_name, "wrongQuestions": wrong_questions}
        prompt = json.dumps(payload, ensure_ascii=False)

        last_issues: list[str] = []
        for attempt in range(2):  # 首次 + 审核不通过重试一次
            feedback = (
                f"\n上一轮输出未通过审核，请修正以下问题后重新输出完整 JSON：{last_issues}"
                if last_issues
                else ""
            )
            raw = llm_transport.chat(prompt + feedback, system=system)
            cards, last_issues = self._clean_reinforcement(raw, wrong_questions)
            if not last_issues:
                return cards
        raise LLMGenerationError(f"强化练习审核未通过（答案不自洽）：{last_issues[:3]}")

    @staticmethod
    def _clean_reinforcement(
        raw: str, wrong_questions: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """解析 + 契约清洗 + critic 自洽审核。返回 (cards, issues)；issues 非空即不通过。"""
        return _clean_reinforcement_payload(raw, wrong_questions)

    # ---- 苏格拉底辅导（接口文档 8.7 / 15.4，B7-a） --------------------------
    def tutor_chat(
        self, *, kp_name: str, history: list[dict[str, str]], message: str
    ) -> dict[str, Any]:
        """整体回复（8.7 JSON 模式）：{reply, suggestions}。

        mock：确定性引导链（不直接给答案，问题收尾）；
        deepseek：真实生成（system 约束引导式），suggestions 可空（契约允许）。
        """
        self._ensure_supported()
        if self.is_mock:
            reply, suggestions = _mock_tutor_reply(message)
            return {"reply": reply, "suggestions": suggestions}
        reply = llm_transport.chat(
            message, system=_TUTOR_SYSTEM.format(kp_name=kp_name), history=history
        )
        return {"reply": reply, "suggestions": []}

    def tutor_chat_stream(
        self, *, kp_name: str, history: list[dict[str, str]], message: str
    ) -> Iterator[str]:
        """流式回复（15.4 SSE）：逐 delta 产出。

        mock：确定性引导链**逐字**流式（字间延迟见 settings.tutor_stream_delay_ms，
        打字机演示效果）；deepseek：经 llm_deepseek.chat_stream 真实流式透传。
        """
        self._ensure_supported()
        if not self.is_mock:
            return llm_transport.chat_stream(
                message, system=_TUTOR_SYSTEM.format(kp_name=kp_name), history=history
            )
        reply, _suggestions = _mock_tutor_reply(message)

        def _char_stream() -> Iterator[str]:
            delay = settings.tutor_stream_delay_ms / 1000
            for char in reply:
                if delay > 0:
                    time.sleep(delay)
                yield char

        return _char_stream()

    def tutor_suggestions(self, message: str) -> list[str]:
        """done 事件的快捷建议（15.4）：mock 按引导链分支；deepseek 可空。"""
        if self.is_mock:
            return _mock_tutor_reply(message)[1]
        return []

    # ---- 文档问答（NotebookLM 式「和文档对话」，严格基于文档 + 溯源，流式） ----
    def doc_chat_stream(
        self,
        *,
        source_title: str,
        contexts: list[str],
        history: list[dict[str, str]],
        message: str,
    ) -> Iterator[str]:
        """就选中文档回答用户问题（逐 delta 流式）。**严格基于文档片段、防幻觉、标出处**。

        与苏格拉底辅导不同：这里是「理解文档」的即问即答（直接作答，不刻意反问）；答案只依据
        传入的文档检索片段（contexts），文档没有的明确回答「文档中未提及」，引用处以 [n] 标注
        对应片段序号（与下方来源列表一一对应）。
        - mock：确定性——由文档要点句合成带 [n] 溯源标记的回答，**逐字**流式（无 Key 全链路可跑）；
        - deepseek：真实流式，system 约束「仅据文档片段作答 + [n] 标注」。
        """
        self._ensure_supported()
        if not self.is_mock:
            joined = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts) if c)[:6000]
            prompt = (
                f"文档标题：{source_title}\n文档片段（回答只能依据这些片段）：\n"
                f"{joined or '（未检索到相关片段）'}\n\n用户问题：{message}"
            )
            return llm_transport.chat_stream(
                prompt, system=_DOC_CHAT_SYSTEM.format(source_title=source_title), history=history
            )
        answer = _mock_doc_answer(source_title, contexts, message)

        def _char_stream() -> Iterator[str]:
            delay = settings.tutor_stream_delay_ms / 1000
            for char in answer:
                if delay > 0:
                    time.sleep(delay)
                yield char

        return _char_stream()

    # ---- 智能辅导·按需资源生成（接口文档 8.8，C-fix 批3-bonus） ---------------
    def suggest_remedial_resources(
        self, kp_name: str, question: str
    ) -> dict[str, Any]:
        """识别问题点 + 给出针对性资源生成清单（8.8）。

        返回 {problemPoint, suggestions:[{id,type,title,expect}]}，type ∈ REMEDIAL_TYPES。
        - mock：关键词识别问题点 + 模板清单；
        - deepseek：真实识别 + 清洗（type 白名单、title/expect 非空，不足回落模板）。
        无 Key 也能跑（mock 兜底）。
        """
        self._ensure_supported()
        if self.is_mock:
            point = _mock_identify_problem(question, kp_name)
            return {"problemPoint": point, "suggestions": _build_remedial_suggestions(point)}
        try:
            return self._deepseek_suggest_remedial(kp_name, question)
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("资源建议真实生成失败，回落模板清单：%s", exc)
            point = _mock_identify_problem(question, kp_name)
            return {"problemPoint": point, "suggestions": _build_remedial_suggestions(point)}

    def _deepseek_suggest_remedial(self, kp_name: str, question: str) -> dict[str, Any]:
        system = (
            f"你是「{kp_name}」的辅导助手。学生提出了一个困惑，请先用一句话精炼概括其"
            "「问题点」，再给出可按需生成的针对性学习资源清单。仅输出 JSON："
            '{"problemPoint": "...", "suggestions": [{"type": "diagram", '
            '"title": "...", "expect": "..."}]}。'
            f"type 只能取 {list(REMEDIAL_TYPES)}（图解/例题/短视频/补充讲义片段），"
            "每种最多 1 项；title 简短、expect 说明预计内容；禁止编造与问题点无关的资源。"
        )
        raw = llm_transport.chat(f"学生困惑：{question}", system=system)
        data = _extract_json(raw)
        point = ""
        suggestions: list[dict[str, Any]] = []
        if isinstance(data, dict):
            point = str(data.get("problemPoint") or "").strip()
            seen: set[str] = set()
            for s in data.get("suggestions") or []:
                if not isinstance(s, dict):
                    continue
                t = s.get("type")
                if t not in REMEDIAL_TYPES or t in seen:
                    continue
                title = str(s.get("title") or "").strip()
                expect = str(s.get("expect") or "").strip()
                if not title or not expect:
                    continue
                seen.add(t)
                suggestions.append({"id": f"r-{t}", "type": t, "title": title, "expect": expect})
        if not point:
            point = _mock_identify_problem(question, kp_name)
        if len(suggestions) < 2:  # 兜底：保证清单可用
            suggestions = _build_remedial_suggestions(point)
        return {"problemPoint": point, "suggestions": suggestions}

    def generate_remedial_content(
        self, kind: str, kp_name: str, problem_point: str
    ) -> dict[str, Any]:
        """按需生成例题 / 补充讲义片段（8.8；diagram/video 复用既有资源服务）。

        kind=='example' → {title, statement, solution}；
        kind=='lecture' → {title, markdown}。mock 确定性 / deepseek 真实 + 兜底。
        """
        self._ensure_supported()
        if self.is_mock:
            return self._mock_remedial_content(kind, kp_name, problem_point)
        try:
            return self._deepseek_remedial_content(kind, kp_name, problem_point)
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("按需资源真实生成失败，回落确定性内容：%s", exc)
            return self._mock_remedial_content(kind, kp_name, problem_point)

    @staticmethod
    def _mock_remedial_content(kind: str, kp_name: str, point: str) -> dict[str, Any]:
        """确定性例题 / 讲义片段（mock / deepseek 兜底）。"""
        if kind == "example":
            return {
                "title": f"例题 · {point}",
                "statement": (
                    f"【例题】围绕「{point}」：请说明它在「{kp_name}」中的作用，"
                    "并用一个具体例子说明其工作过程。"
                ),
                "solution": (
                    f"解析：\n1. 先明确「{point}」的定义与要解决的问题；\n"
                    f"2. 结合「{kp_name}」的整体流程，定位它处于哪一步、起什么作用；\n"
                    "3. 举一个最小例子，代入数据走一遍，观察输入到输出的变化；\n"
                    f"小结：抓住「{point}」的本质，即可举一反三。"
                ),
            }
        # 默认 lecture 片段
        return {
            "title": f"补充讲义 · {point}",
            "markdown": (
                f"# 补充讲义 · {point}\n\n"
                f"> 针对你在「{kp_name}」中卡住的「{point}」，这里做一段精炼补充。\n\n"
                f"## 一、它解决什么问题\n\n「{point}」是「{kp_name}」的关键一环——"
                "先理解它「要做什么」，再看「怎么做」。\n\n"
                "## 二、关键要点\n\n"
                f"- 抓住「{point}」的输入、变换与输出三段式；\n"
                "- 留意它与相邻概念的衔接关系（前一步给它什么、它给后一步什么）；\n"
                "- 用一个最小例子复现，建立可迁移的直觉。\n\n"
                f"## 三、一句话小结\n\n理解「{point}」的本质，就抓住了这一段的核心。"
            ),
        }

    def _deepseek_remedial_content(
        self, kind: str, kp_name: str, problem_point: str
    ) -> dict[str, Any]:
        """真实例题 / 讲义片段 + 契约清洗。"""
        if kind == "example":
            system = (
                f"你是「{kp_name}」的辅导老师。针对学生的问题点「{problem_point}」出一道例题"
                '并给出分步解析，仅输出 JSON：{"title": "...", "statement": "...", "solution": "..."}。'
                "statement 为题干、solution 为分步解析，紧扣问题点，简体中文。"
            )
            raw = llm_transport.chat(f"问题点：{problem_point}", system=system)
            data = _extract_json(raw)
            if not isinstance(data, dict) or not str(data.get("statement") or "").strip():
                raise LLMGenerationError("例题输出无法解析为契约 JSON")
            return {
                "title": str(data.get("title") or f"例题 · {problem_point}").strip(),
                "statement": str(data["statement"]).strip(),
                "solution": str(data.get("solution") or "").strip() or "（解析略）",
            }
        system = (
            f"你是「{kp_name}」的讲义作者。针对学生的问题点「{problem_point}」写一段精炼的补充"
            '讲义片段（Markdown），仅输出 JSON：{"title": "...", "markdown": "..."}。'
            "markdown 用二级标题分节、紧扣问题点、200-400 字，简体中文。"
        )
        raw = llm_transport.chat(f"问题点：{problem_point}", system=system)
        data = _extract_json(raw)
        if not isinstance(data, dict) or not str(data.get("markdown") or "").strip():
            raise LLMGenerationError("讲义片段输出无法解析为契约 JSON")
        return {
            "title": str(data.get("title") or f"补充讲义 · {problem_point}").strip(),
            "markdown": str(data["markdown"]).strip(),
        }

    # ---- 外部资源·联网搜索聚合（接口文档 8.6 增量，C-fix 批3-bonus） ----------
    def aggregate_resources(
        self,
        kp_name: str,
        weak_points: list[str],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """聚合 Agent 整理 + critic 评分（接口文档 8.6）。

        candidates 为联网搜索命中（或种子兜底）[{title,url,source,snippet,type,embed?,duration?}]，
        输出按相关度降序的 8.6 资源清单 [{id,type,title,source,url,relevance,credibility,reason,embed?,duration?}]。
        - mock：确定性评分（字符二元组相关度 + 来源可信度启发式 + 模板理由）；
        - deepseek：真实排序/评分/理由 + 契约清洗（**URL 必须取自候选，杜绝幻觉链接**）。
        """
        self._ensure_supported()
        cands = [
            c for c in candidates
            if isinstance(c, dict) and str(c.get("url") or "").strip() and str(c.get("title") or "").strip()
        ]
        if not cands:
            return []
        if self.is_mock:
            return self._mock_aggregate(kp_name, weak_points, cands)
        try:
            return self._deepseek_aggregate(kp_name, weak_points, cands)
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("资源聚合评分真实生成失败，回落确定性评分：%s", exc)
            return self._mock_aggregate(kp_name, weak_points, cands)

    @staticmethod
    def _agg_item(cand: dict[str, Any], idx: int, *, type_: str, rel: int, cred: int, reason: str) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": f"agg-{idx}",
            "type": type_ if type_ in _AGG_TYPES else (cand.get("type") if cand.get("type") in _AGG_TYPES else "文档"),
            "title": str(cand.get("title")),
            "source": str(cand.get("source") or "web"),
            "url": str(cand.get("url")),
            "relevance": rel,
            "credibility": cred,
            "reason": reason,
        }
        if cand.get("embed"):
            item["embed"] = cand["embed"]
        if cand.get("duration"):
            item["duration"] = cand["duration"]
        return item

    @staticmethod
    def _ensure_video(ranked: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
        """结果选取约束（形态多样性保底）：Top-N 至少含 1 个 type=视频 的资源。

        ranked 已按 relevance 降序。Top-N 无视频时，从落选候选中取评分最高的视频，
        替换 Top-N 中评分最低的非视频项（总数不变），再按相关度重排保持降序；
        候选池确实无视频 → 不硬塞，记日志作可解释降级。
        """
        top = ranked[:limit]
        if any(it["type"] == "视频" for it in top):
            return top
        video = next((it for it in ranked[limit:] if it["type"] == "视频"), None)
        if video is None:
            logger.info("资源聚合：候选池无任何视频资源，视频保底降级（本次清单不含视频）")
            return top
        dropped = top.pop()  # 已降序 → 末位即评分最低项（且必为非视频）
        top.append(video)
        top.sort(key=lambda x: x["relevance"], reverse=True)
        logger.info(
            "资源聚合：Top-%d 无视频，以「%s」(rel=%s) 替换最低分项「%s」(rel=%s)",
            limit, video["title"], video["relevance"], dropped["title"], dropped["relevance"],
        )
        return top

    @staticmethod
    def _mock_aggregate(
        kp_name: str, weak_points: list[str], cands: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """确定性聚合评分（字符二元组相关度 + 来源可信度 + 模板理由）。"""
        target = _char_bigrams(kp_name + "".join(weak_points))
        wp = weak_points[0] if weak_points else kp_name
        scored: list[tuple[int, dict[str, Any]]] = []
        for i, c in enumerate(cands, start=1):
            text = f"{c.get('title', '')} {c.get('snippet', '')} {c.get('source', '')}"
            overlap = (len(target & _char_bigrams(text)) / len(target)) if target else 0.0
            rel = max(60, min(99, round(60 + overlap * 39)))
            cred = _credibility_of(str(c.get("source", "")), str(c.get("url", "")))
            reason = f"契合你当前「{kp_name}」的学习，对补强「{wp}」很有帮助。"
            scored.append((rel, LLMClient._agg_item(c, i, type_=str(c.get("type") or ""), rel=rel, cred=cred, reason=reason)))
        scored.sort(key=lambda e: e[0], reverse=True)
        return LLMClient._ensure_video([it for _, it in scored])

    def _deepseek_aggregate(
        self, kp_name: str, weak_points: list[str], cands: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """真实聚合排序/评分/理由 + 契约清洗（URL 白名单防幻觉）。"""
        by_url = {str(c["url"]): c for c in cands}
        listing = [
            {
                "title": c.get("title"),
                "url": c.get("url"),
                "source": c.get("source"),
                "snippet": str(c.get("snippet") or "")[:200],
                "type": c.get("type"),
            }
            for c in cands
        ]
        system = (
            "你是学习资源聚合与审核 Agent。从候选搜索结果中筛选并排序出对该学习者最有价值的优质"
            '资源，仅输出 JSON：{"items": [{"type": "视频", "title": "...", "source": "...", '
            '"url": "...", "relevance": 0, "credibility": 0, "reason": "..."}]}。'
            f"type 只能取 {list(_AGG_TYPES)}；relevance/credibility 为 0-100 整数（相关度结合"
            "知识点与薄弱点、可信度结合来源权威性）；reason 一句中文说明为何推荐（结合薄弱点）；"
            "**url 必须原样取自候选列表，禁止编造或改写链接**；按 relevance 降序，最多 8 条；"
            "资源形态尽量多样（视频/课程/论文/文档兼顾），候选中存在视频时清单**至少含 1 条视频**。"
        )
        payload = {"knowledgePoint": kp_name, "weakPoints": weak_points, "candidates": listing}
        raw = llm_transport.chat(json.dumps(payload, ensure_ascii=False), system=system)
        data = _extract_json(raw)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise LLMGenerationError("资源聚合输出无法解析为契约 JSON")
        items: list[dict[str, Any]] = []
        for it in data["items"]:
            if not isinstance(it, dict):
                continue
            url = str(it.get("url") or "").strip()
            cand = by_url.get(url)
            if cand is None:  # 防幻觉：只接受候选列表内的真实 URL
                continue
            reason = str(it.get("reason") or "").strip() or "与当前学习高度相关。"
            merged = dict(cand)
            if it.get("title"):
                merged["title"] = it["title"]
            if it.get("source"):
                merged["source"] = it["source"]
            items.append(
                LLMClient._agg_item(
                    merged,
                    len(items) + 1,
                    type_=str(it.get("type") or ""),
                    rel=_clamp_score(it.get("relevance"), 75),
                    cred=_clamp_score(it.get("credibility"), 85),
                    reason=reason,
                )
            )
        if not items:
            raise LLMGenerationError("资源聚合输出不含有效候选")
        # 视频保底：LLM 清单一条视频都没有而候选池有 → 用确定性评分把最优视频候选补入
        # 排序池（质量仍由启发式评分把关），交给 _ensure_video 统一做 Top-N 替换。
        if not any(it["type"] == "视频" for it in items):
            chosen_urls = {it["url"] for it in items}
            video_cands = [
                c for c in cands
                if str(c.get("type")) == "视频" and str(c["url"]) not in chosen_urls
            ]
            if video_cands:
                best = self._mock_aggregate(kp_name, weak_points, video_cands)[0]
                best["id"] = f"agg-{len(items) + 1}"
                items.append(best)
        items.sort(key=lambda x: x["relevance"], reverse=True)
        return self._ensure_video(items)

    # ---- 康奈尔线索生成（接口文档 18.1，C2） -------------------------------
    def generate_cornell_cues(
        self, kp_id: str, kp_name: str, difficulty: str, description: str = ""
    ) -> dict[str, Any]:
        """生成康奈尔笔记线索（接口文档 18.1）。

        返回 {cues:[{id,type,text}], noteOutline:[{id,cueId,heading,points}],
        summaryHint, sources}（sources 复用 8.2 讲义 RAG 引用占位 _LECTURE_SOURCES）。
        - mock：确定性主题模板（已收录知识点精写线索，未收录参数化生成），cues 5-8 条；
        - deepseek：真实生成 + 契约清洗（cues 5-8 条、type 枚举、要点截断）；解析失败
          或上游异常 → 回落确定性主题模板（线索始终可用，不向路由抛 2001）。
        """
        self._ensure_supported()
        if self.is_mock:
            return self._mock_cornell(kp_id, kp_name, difficulty, description)
        try:
            return self._deepseek_cornell(kp_id, kp_name, difficulty, description)
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("康奈尔线索真实生成失败，回落主题模板：%s", exc)
            return self._mock_cornell(kp_id, kp_name, difficulty, description)

    @staticmethod
    def _assemble_cornell(
        cue_specs: list[tuple[str, str]],
        outline_specs: list[tuple[str, list[str]]],
        summary_hint: str,
    ) -> dict[str, Any]:
        """把 (cues, outline, summaryHint) 装配为契约结构（id 编号 + cueId 关联）。"""
        return _assemble_cornell_payload(
            cue_specs, outline_specs, summary_hint, _LECTURE_SOURCES
        )

    def _mock_cornell(
        self, kp_id: str, kp_name: str, difficulty: str, description: str
    ) -> dict[str, Any]:
        """确定性康奈尔线索（mock / deepseek 兜底共用）。"""
        return _generate_mock_cornell(
            kp_id, kp_name, difficulty, description, _LECTURE_SOURCES
        )

    def _deepseek_cornell(
        self, kp_id: str, kp_name: str, difficulty: str, description: str
    ) -> dict[str, Any]:
        """真实康奈尔线索生成 + 契约清洗（接口文档 18.1）。"""
        system = (
            "你是康奈尔笔记法的教学设计助手。针对给定知识点，产出供学生做笔记的"
            "「线索区」关键问题/关键词，以及「主笔记区」要点预填骨架。仅输出 JSON："
            '{"cues":[{"type":"question|keyword","text":"..."}],'
            '"noteOutline":[{"heading":"要点标题","points":["要点1","要点2"]}],'
            '"summaryHint":"总结区引导语"}。'
            "要求：cues 为 5-8 条（多为启发性问题、少量关键词）；noteOutline 2-4 条，"
            "每条 points 2-4 个；所有内容必须紧扣该知识点主题、简体中文、不要跑题；只输出 JSON。"
        )
        prompt = f"知识点：{kp_name}\n难度档：{difficulty}\n知识点说明：{description or kp_name}"
        raw = llm_transport.chat(prompt, system=system)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise LLMGenerationError("康奈尔线索输出无法解析为契约 JSON")
        return self._clean_cornell(data)

    @staticmethod
    def _clean_cornell(data: dict[str, Any]) -> dict[str, Any]:
        """康奈尔线索契约清洗：cues 5-8 条、type 枚举、noteOutline 要点截断。"""
        return _clean_cornell_payload(data, _LECTURE_SOURCES)

    # ---- 费曼讲解评估（接口文档 18.2，C2；复用苏格拉底 Agent 的引导式取向） ------
    def feynman_eval(
        self,
        *,
        kp_id: str,
        kp_name: str,
        description: str,
        history: list[dict[str, str]],
        explanation: str,
    ) -> dict[str, Any]:
        """评估学生对某知识点的「费曼讲解」（接口文档 18.2）。

        返回 {feedback, gaps, score, followups, complete}——gaps 元素为
        {kpId, title, detail, severity}（应回看资源 review[] 由服务层按 kp 注入）。
        - mock：确定性评估——按 _FEYNMAN_CONCEPTS 比对学生讲解，定位「讲漏」的关键
          概念为 gaps，覆盖率折算 score，无随机、无网络；
        - deepseek：真实评估 + 契约清洗（severity 枚举、score 截断、gaps 标题非空），
          解析失败/上游异常 → 回落确定性评估（评估始终可用，不向路由抛 2001）。
        """
        self._ensure_supported()
        if self.is_mock:
            return self._mock_feynman(kp_id, kp_name, explanation)
        try:
            return self._deepseek_feynman(kp_id, kp_name, description, history, explanation)
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("费曼讲解评估真实生成失败，回落确定性评估：%s", exc)
            return self._mock_feynman(kp_id, kp_name, explanation)

    @staticmethod
    def _mock_feynman(kp_id: str, kp_name: str, explanation: str) -> dict[str, Any]:
        """确定性费曼评估：比对应覆盖概念，定位讲漏点为 gaps。"""
        return _mock_feynman_evaluation(kp_id, kp_name, explanation)

    def _deepseek_feynman(
        self,
        kp_id: str,
        kp_name: str,
        description: str,
        history: list[dict[str, str]],
        explanation: str,
    ) -> dict[str, Any]:
        """真实费曼评估 + 契约清洗（接口文档 18.2）。"""
        system = (
            "你是费曼学习法导师。学生会用自己的话讲解一个知识点，你要：① 点评其讲解的"
            "亮点与问题；② 找出讲错或讲漏的关键知识点（gaps）；③ 给出引导学生补讲的追问，"
            "但不要直接替学生把答案讲完。仅输出 JSON："
            '{"feedback":"点评(简体中文,不超过150字)","score":0到100整数(讲解完整度),'
            '"gaps":[{"title":"缺口标题","detail":"为什么这是缺口","severity":"high|medium|low"}],'
            '"followups":["引导补讲的追问"],"complete":true或false(是否已无 high/medium 缺口)}。只输出 JSON。'
        )
        prompt = (
            f"知识点：{kp_name}\n知识点说明：{description or kp_name}\n"
            f"学生讲解：{explanation}"
        )
        raw = llm_transport.chat(prompt, system=system, history=history)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise LLMGenerationError("费曼评估输出无法解析为契约 JSON")
        return self._clean_feynman(data, kp_id)

    @staticmethod
    def _clean_feynman(data: dict[str, Any], kp_id: str) -> dict[str, Any]:
        """费曼评估契约清洗：gaps 标题/说明非空、severity 枚举、score 截断、complete 布尔。

        gap.kpId 一律回正为当前知识点 id（不信任模型给的 kp，防幻觉引用）。
        """
        return _clean_feynman_payload(data, kp_id)

    # ---- 对话式画像抽取（接口文档 17.1，C1-b） ------------------------------
    def extract_portrait(
        self,
        *,
        message: str,
        context: dict[str, Any] | None,
        first_turn: bool,
    ) -> list[dict[str, Any]]:
        """从单轮自然语言抽取画像维度增量（接口文档 17.1 portraitUpdates）。

        返回 PortraitDimension[]（已契约清洗：key 白名单、source 枚举、
        inferred 低 confidence、无信号不编造）。
        - mock：确定性关键词抽取（无随机、无网络）；
        - deepseek：真实抽取 + 契约清洗（防幻觉 system 约束）。
        供 DialogueDiagnosticAgent 调用；问题编排策略在 Agent 侧（确定性）。
        """
        self._ensure_supported()
        if self.is_mock:
            return _mock_extract_portrait(message, context, first_turn)
        return self._deepseek_extract_portrait(message, context)

    def _deepseek_extract_portrait(
        self, message: str, context: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """真实画像维度抽取 + 契约清洗（17.1 防幻觉约束）。"""
        system = (
            "你是对话式学习画像抽取专家。从学生的自然语言中抽取**可确定**的画像维度，"
            '仅输出 JSON：{"updates":[{"key":"...","label":"...","value":"...",'
            '"score":0,"confidence":0.0,"source":"dialogue|inferred"}]}。'
            f"key 只能取自固定维度集 {list(_PORTRAIT_KEYS)}，label 用对应中文名；"
            "value 为简短中文描述；仅 knowledge_base 等可量化维度给 score（0-100 整数），"
            "其余维度省略 score；confidence 为 0-1 小数；"
            "学生明确陈述的维度用 source=dialogue，由上下文间接推断的用 source=inferred "
            "且 confidence≤0.6；无法从文本判断的维度一律不要输出（禁止编造）。"
        )
        payload = {"studentMessage": message, "knownContext": context or {}}
        raw = llm_transport.chat(
            json.dumps(payload, ensure_ascii=False), system=system
        )
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise LLMGenerationError("画像维度抽取输出无法解析为契约 JSON")
        return _sanitize_portrait_updates(data.get("updates"))

    # ---- 学习路径规划（接口文档 6.2，真实规划 Agent 的叙述层） ----------------
    def plan_path(
        self,
        *,
        profile: dict[str, Any],
        steps: list[dict[str, Any]],
        deterministic: bool = False,
    ) -> dict[str, Any]:
        """为已排序的路径步骤生成「为什么这样排」的理由 + 整体摘要。

        排序/优先级由 agents.planner_agent 按画像+掌握度确定性计算（科学排程），
        本方法只负责把每步的排程信号（signals）转成可读理由：
        - mock：按信号确定性模板（无随机、无网络，不同画像/掌握度 → 不同理由）；
        - deepseek：真实生成 + 契约清洗（reason 按 kpId 回填，缺失回落模板），
          解析失败/上游异常 → 回落 mock，保证演示稳定（不向路由抛 2001）。
        - deterministic=True：**强制走确定性模板、零网络**（供 GET /learning-path 实时
          重算用——画像变即重算、不阻塞页面、不计费）。

        入参 steps[i]：{kpId, topic, order, status, signals:{weak,mastered,
        foundational,jobBoost}}；profile：{foundationLevel, goal, pace, jobName}。
        返回 {reasons: {kpId: reason}, summary: str}。
        """
        self._ensure_supported()
        if deterministic or self.is_mock:
            return self._mock_plan_path(profile, steps)
        try:
            return self._deepseek_plan_path(profile, steps)
        except LLMGenerationError as exc:
            generation_provenance.mark_degraded()
            logger.warning("路径规划理由真实生成失败，回落确定性模板：%s", exc)
            return self._mock_plan_path(profile, steps)

    @staticmethod
    def _mock_plan_path(
        profile: dict[str, Any], steps: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """确定性规划理由（mock / deepseek 兜底共用）。按排程信号分支产出理由。"""
        job_name = (profile.get("jobName") or "").strip()
        reasons: dict[str, str] = {}
        weak_count = 0
        mastered_count = 0

        def _ability(sig: dict[str, Any]) -> str:
            s = sig.get("abilityScore")
            return f"（微测能力分 {int(s)}）" if isinstance(s, (int, float)) and not isinstance(s, bool) else ""

        for step in steps:
            sig = step.get("signals") or {}
            topic = step.get("topic") or step.get("kpId")
            order = step.get("order")
            if sig.get("mastered"):
                mastered_count += 1
                reasons[step["kpId"]] = (
                    f"你已掌握「{topic}」（测验通过），后置到第 {order} 步用于巩固复习，可快速跳过。"
                )
            elif sig.get("proficient"):
                # 微测能力分达标但未正式通过测验 → 后置略读（靠测：用实测分判定"已会"）
                mastered_count += 1
                reasons[step["kpId"]] = (
                    f"微测显示你对「{topic}」已基本达标{_ability(sig)}，后置到第 {order} 步快速复习、可略读。"
                )
            elif sig.get("jobBoost") and sig.get("weak"):
                weak_count += 1
                lead = f"目标岗位「{job_name}」" if job_name else "你的目标岗位"
                reasons[step["kpId"]] = (
                    f"{lead}对「{topic}」要求高且你尚薄弱{_ability(sig)}，按先修顺序排在第 {order} 步并列为重点强化项。"
                )
            elif sig.get("weak") and sig.get("foundational"):
                weak_count += 1
                reasons[step["kpId"]] = (
                    f"「{topic}」是后续内容的基础且你尚未掌握{_ability(sig)}，优先安排在第 {order} 步打牢根基。"
                )
            elif sig.get("weak"):
                weak_count += 1
                reasons[step["kpId"]] = (
                    f"「{topic}」是你的薄弱点{_ability(sig)}，按先修顺序安排在第 {order} 步集中攻克。"
                )
            else:
                reasons[step["kpId"]] = (
                    f"按知识先修依赖，「{topic}」承接前序内容，安排在第 {order} 步进阶。"
                )
        level = profile.get("foundationLevel") or "中等"
        style = (profile.get("style") or "").strip()
        pace_type = (profile.get("paceType") or "").strip()
        how = ""
        if style or pace_type:
            tag = "·".join(t for t in (style, pace_type) if t)
            how = f"；并按你的学习偏好（{tag}）为每步默认推荐最适合的资源形式"
        summary = (
            f"本路径依据你的画像（基础{level}）与各知识点实测能力规划：将 {weak_count} 个薄弱/未掌握点"
            f"按先修顺序前置，{mastered_count} 个已达标点后置复习，共 {len(steps)} 步{how}，"
            "每步配套讲义/思维导图/图解/视频/题库资源。"
        )
        return {"reasons": reasons, "summary": summary}

    def _deepseek_plan_path(
        self, profile: dict[str, Any], steps: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """真实规划理由生成 + 契约清洗（reason 按 kpId 回填，缺失回落模板）。"""
        system = (
            "你是个性化学习路径规划专家。给定学习者画像与一条**已排好序**的学习路径"
            "（每步含 kpId/topic/order/status 及排程信号 signals），为每一步用一句话"
            "解释「为什么排在这个位置」，并给出整体路径摘要。仅输出 JSON："
            '{"reasons":[{"kpId":"...","reason":"..."}],"summary":"..."}。'
            "理由必须扣住该步信号：weak=薄弱点优先、mastered=已掌握后置复习、"
            "foundational=先修基础、jobBoost=目标岗位高需求前置；语言简体中文、"
            "每条不超过 50 字；不得改变给定顺序，只解释顺序；只输出 JSON。"
        )
        payload = {"profile": profile, "steps": steps}
        raw = llm_transport.chat(json.dumps(payload, ensure_ascii=False), system=system)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise LLMGenerationError("路径规划输出无法解析为契约 JSON")
        valid_ids = {s["kpId"] for s in steps}
        reasons: dict[str, str] = {}
        for item in data.get("reasons") or []:
            if not isinstance(item, dict):
                continue
            kid = item.get("kpId")
            reason = str(item.get("reason") or "").strip()
            if kid in valid_ids and reason:
                reasons[kid] = reason
        # 缺失步骤回落确定性模板（保证每步都有理由）
        fallback = self._mock_plan_path(profile, steps)
        for step in steps:
            reasons.setdefault(step["kpId"], fallback["reasons"][step["kpId"]])
        summary = str(data.get("summary") or "").strip() or fallback["summary"]
        return {"reasons": reasons, "summary": summary}

    # ---- 通用补全（B5-a：供 Agent 节点调用） -------------------------------
    def complete(
        self,
        prompt: str,
        *,
        agent_id: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """通用补全入口（B5-a 工作流骨架）。

        Agent 先经 services.prompts.get_template() 现读模板并渲染占位符，
        再把渲染后的 prompt 交本方法。mock provider 按 agent 类型返回
        **确定性结构化输出**（无随机、无网络）；variables 仅供 mock 产出
        与输入相关的确定性内容，真实 provider（B5-b）忽略该参数、只发 prompt。
        """
        self._ensure_supported()
        variables = variables or {}
        if not self.is_mock:
            return self._deepseek_complete(prompt, agent_id)
        if agent_id == "diagnosis":
            return self._mock_diagnosis(variables)
        if agent_id == "generation":
            return self._mock_generation(variables)
        if agent_id == "critic":
            return self._mock_critic(variables)
        raise ValueError(f"未知 agent_id：{agent_id}（固定 3 项：diagnosis/generation/critic）")

    def _deepseek_complete(self, prompt: str, agent_id: str) -> dict[str, Any]:
        """真实 Agent 补全：只发渲染后 prompt + 按 agent 类型的输出约束。"""
        if agent_id == "generation":
            text = llm_transport.chat(
                prompt,
                system="你是领域知识讲义生成专家。直接输出 Markdown 讲义正文，"
                "不要输出讲义之外的解释或前后缀。",
            )
            return {"markdown": _strip_md_fence(text)}
        if agent_id == "diagnosis":
            text = llm_transport.chat(
                prompt,
                system='请仅输出 JSON：{"weakKpIds": ["知识点id"], '
                '"summary": "一句话诊断摘要", "reasoning": "诊断依据"}',
            )
            data = _extract_json(text)
            if isinstance(data, dict) and data.get("summary"):
                weak = [str(k) for k in (data.get("weakKpIds") or []) if k]
                return {
                    "weakKpIds": weak,
                    "summary": str(data["summary"]),
                    "reasoning": str(data.get("reasoning") or ""),
                }
            # 非 JSON 输出 → 自由文本兜底，不中断工作流
            return {"weakKpIds": [], "summary": text[:100], "reasoning": text}
        if agent_id == "critic":
            raise LLMGenerationError(
                "critic 在真实模式走本地逐句接地校验（agents.critic_agent），不经 LLM 通道"
            )
        raise ValueError(f"未知 agent_id：{agent_id}（固定 3 项：diagnosis/generation/critic）")

    @staticmethod
    def _mock_diagnosis(variables: dict[str, Any]) -> dict[str, Any]:
        """诊断 Agent mock：基于**该用户真实画像 + 掌握度**定位薄弱点（因人而异）。

        - 薄弱点：目标 kp + 该用户尚未通过（status≠passed）的其它知识点；不足 3 项时
          以固定后备维度补齐，保证输出稳定；
        - 诊断依据（reasoning）引用该用户画像摘要与掌握度计数，不同用户 / 不同掌握度
          → 文案不同，不再是全局同一句模板。
        """
        return _mock_workflow_diagnosis(variables)

    def _mock_generation(self, variables: dict[str, Any]) -> dict[str, Any]:
        """生成 Agent mock：确定性递进讲义产出（按 kpName/难度 + 画像能力档 depthTier）。

        depthTier 由工作流 generation_node 依当前用户画像派生（advanced/beginner/basic）；
        直出 /resource/lecture 不传 → tier=None 走难度基线（与 B5b/B10 契约一致）。
        """
        return _mock_workflow_generation(variables, self._lecture_markdown)

    @staticmethod
    def _mock_critic(variables: dict[str, Any]) -> dict[str, Any]:
        """审核 Agent mock：默认通过；测试钩子可强制低分（验证重试/降级路径）。"""
        return _mock_workflow_critic(variables, _LECTURE_HALLUCINATION_RATE)

    # ---- R4.3 Tool Loop：结构化工具决策（tool_call / final_answer 双路径）------

    def decide_tool(
        self,
        *,
        task: str,
        agent_id: str,
        allowed_tools: list[dict[str, Any]] | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """让模型在**结构化双路径**中二选一（Tool Loop 的决策出口）。

        产出严格二选一，**不允许**把自然语言里"想调用工具"当成调用：

        - ``{"kind": "tool_call", "tool": <name>, "arguments": {...}}``
        - ``{"kind": "final_answer", "answer": "<文本>"}``

        Args:
            task: 当前任务/用户诉求。
            agent_id: 决策主体（generation / critic）。
            allowed_tools: 可用工具描述列表（``ToolSpec.to_dict()`` 形状，
                含 ``argsSchema``），**只会把已授权工具暴露给模型**。
            history: 本轮已发生的 ``tool_call`` / ``observation`` 记录。

        Returns:
            上述两种结构之一。

        Raises:
            LLMGenerationError: 真实模式下模型输出不符合契约
                （**由 Tool Loop 捕获并回喂模型**，不会让整个 Loop 崩溃）。
        """
        self._ensure_supported()
        tools = list(allowed_tools or [])
        turns = list(history or [])
        if self.is_mock:
            return self._mock_decide_tool(task, agent_id, tools, turns)
        return self._deepseek_decide_tool(task, agent_id, tools, turns)

    @staticmethod
    def _mock_decide_tool(
        task: str,
        agent_id: str,
        allowed_tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """mock 决策：**确定性**地按顺序调用尚未用过的工具，用尽即收敛作答。

        无随机、无网络 → 同输入同输出；且必然在有限步内给出 ``final_answer``，
        不会陷入死循环。
        """
        if not allowed_tools:
            return {
                "kind": "final_answer",
                "answer": f"（mock {agent_id}）当前无可用工具，直接作答：{task}",
            }
        used = {
            str(item.get("tool"))
            for item in history
            if str(item.get("kind") or "") == "tool_call"
        }
        for spec in allowed_tools:
            name, _schema = _tool_descriptor(spec)
            if not name or name in used:
                continue
            return {
                "kind": "tool_call",
                "tool": name,
                "arguments": _mock_tool_arguments(spec, task),
            }
        return {
            "kind": "final_answer",
            "answer": f"（mock {agent_id}）已调用 {len(used)} 个工具，收敛作答：{task}",
        }

    @staticmethod
    def _deepseek_decide_tool(
        task: str,
        agent_id: str,
        allowed_tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """真实决策：把可用工具 schema 交给模型，要求只回一个 JSON 对象。"""
        system = (
            "你是工具调用决策器，只能输出一个 JSON 对象，禁止任何其它文本或 markdown 围栏。\n"
            "必须在以下两种结构中二选一：\n"
            '1) 需要更多信息时：{"kind":"tool_call","tool":"<可用工具名>","arguments":{...}}\n'
            '2) 信息已足够时：{"kind":"final_answer","answer":"<面向用户的最终答复>"}\n'
            "只有当缺少完成任务所需信息时才调用工具；否则直接 final_answer。\n"
            "只允许使用「可用工具」列表中的名字，参数必须符合其 argsSchema。\n"
            f"可用工具：{json.dumps(allowed_tools, ensure_ascii=False, default=str)}"
        )
        prompt = json.dumps(
            {"task": task, "agentId": agent_id, "history": history},
            ensure_ascii=False,
            default=str,
        )
        text = llm_transport.chat(prompt, system=system)
        data = _extract_json(text)
        if isinstance(data, dict) and data.get("kind") == "tool_call":
            tool = str(data.get("tool") or "").strip()
            if not tool:
                raise LLMGenerationError("工具决策缺少 tool 字段")
            args = data.get("arguments")
            return {"kind": "tool_call", "tool": tool, "arguments": args if isinstance(args, dict) else {}}
        if isinstance(data, dict) and data.get("kind") == "final_answer":
            answer = str(data.get("answer") or "").strip()
            if not answer:
                raise LLMGenerationError("final_answer 缺少 answer 字段")
            return {"kind": "final_answer", "answer": answer}
        raise LLMGenerationError("工具决策不符合结构化契约（需 kind=tool_call 或 final_answer）")


# 内容安全中心钝化点：类定义完成后统一注册全部生成出口，不改变公共方法签名。
content_safety.install_llm_client_guard(LLMClient)


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """返回进程内 LLMClient 单例（按当前 settings.llm_provider）。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
