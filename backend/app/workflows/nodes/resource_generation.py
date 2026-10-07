"""资源生成子图的 LangGraph 节点（R2：Workflow 迁移）。

拓扑（与迁移前逐字一致）：

```
diagnostic → retrieval → generation → validation ─┬→ decision → END
                                       ▲          │
                                       └── retry ≤2 ──┘（评分 < 阈值）
仍不过 → 降级输出：final_output 标记 validation_score 与 degraded:true。
```

## 本模块的三条纪律

1. **包装，不重写**：每个节点仍调用既有的 `run_diagnostic` / `retriever` /
   `run_generator` / `run_critic` 与 `_portrait_summary` / `_ability_tier` 等工具，
   业务逻辑一行未改；节点只负责**读 State → 调业务 → 返回 State 增量**。
2. **State 只进不出**：输入是完整的 `MultiAgentState`，输出是**部分** dict。
   嵌套字段靠 `merge_dict` 合并，故节点只写自己关心的子字段；
   `execution.nodeLog` 是嵌套列表，按 `[*已有的, 新增]` 整体替换（既覆盖又追加的
   二义性是刻意避免的）。
3. **消息走 `AgentMessage`**：不再手拼 11.2 字符串。展示所需的
   `from/to/message/type` 经 `app.core.trace.make_message` 存进
   `metadata["display"]`，**入参与迁移前 `_msg()` 逐字一致**，投影后 11.2 文案不变。

## 常量归属

`VALIDATION_THRESHOLD` / `MAX_RETRIES` / `Retriever` 定义在本模块，由
`app.workflows.learning_workflow` re-export —— 那样 `nodes → learning_workflow`
与 `learning_workflow → nodes` 不会互为循环。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.critic_agent import PASS_SCORE, run_critic
from app.agents.diagnostic_agent import run_diagnostic
from app.agents.generator_agent import run_generator
from app.agents.tool_loop import ToolLoop
from app.agents.tools.registry import RAG_TOOL_NAME
from app.core.config import settings
from app.core.trace import PROMPT_EXCERPT_LEN, make_message
from app.services import mastery as mastery_service
from app.services import student_portrait as portrait_service
from app.workflows.state import MultiAgentState

logger = logging.getLogger(__name__)

# critic 评分阈值：validationScore < 阈值 → 回 generator 重试。
# 与 agents.critic_agent.PASS_SCORE 同源（单一真值源），不再各处写 0.8。
VALIDATION_THRESHOLD: float = PASS_SCORE
# 最大重试次数（首次生成不计）：重试 2 次仍不过 → 降级
MAX_RETRIES = 2

Retriever = Callable[[str, str, str], list[dict[str, Any]]]

# 知识点 → 画像能力维度（用于据当前用户在该维度的掌握分派讲义深度档）
_KP_ABILITY: dict[str, str] = {
    "ml": "机器学习基础", "nn": "神经网络", "dl": "深度学习",
    "cnn": "深度学习", "transformer": "Transformer",
    "finetune": "大模型微调", "attention": "注意力机制",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _portrait_summary(portrait: dict[str, Any]) -> str:
    """异质学生画像 → 诊断 prompt 用的 profileSummary 文本（因人而异）。"""
    dims = portrait.get("dimensions") or []
    parts: list[str] = []
    for d in dims:
        label = d.get("label") or d.get("key") or ""
        if not label:
            continue
        score = d.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            parts.append(f"{label} {int(score)}分")
        else:
            value = (d.get("value") or "").strip()
            if value:
                parts.append(f"{label}：{value}")
    return "；".join(parts) if parts else "画像尚未采集（按通用基线诊断）"


def _ability_tier(portrait: dict[str, Any], kp_id: str, kp_name: str) -> str | None:
    """据画像能力维度派生当前知识点的讲义深度档：advanced / beginner / basic / None。"""
    dims = portrait.get("dimensions") or []
    ability = [
        d for d in dims
        if isinstance(d.get("score"), (int, float)) and not isinstance(d.get("score"), bool)
    ]
    if not ability:
        return None
    target_label = _KP_ABILITY.get(kp_id)
    score: float | None = None
    if target_label:
        for d in ability:
            label = d.get("label") or d.get("key") or ""
            if target_label in label or (label and label in target_label) or target_label in kp_name:
                score = float(d["score"])
                break
    if score is None:
        score = sum(float(d["score"]) for d in ability) / len(ability)
    if score >= 70:
        return "advanced"
    if score <= 35:
        return "beginner"
    return "basic"


def _mock_retriever(kp_id: str, kp_name: str, difficulty: str) -> list[dict[str, Any]]:
    """确定性 mock 切片：结构对齐 B3 检索器输出（id/content/metadata/score）。"""
    sources = [
        ("doc_001", "《深度学习》(花书) 相关章节", f"{kp_name}的核心概念与数学定义：输入经参数化变换映射为输出表示。"),
        ("doc_002", "Stanford 公开课讲义", f"{kp_name}的典型实现与{difficulty}难度示例代码讲解。"),
        ("doc_003", "领域权威综述", f"{kp_name}的工程实践要点、常见误区与调优建议。"),
    ]
    return [
        {
            "id": f"{kp_id}_chunk_{i + 1}",
            "content": content,
            "metadata": {"docId": doc_id, "title": title, "kpId": kp_id},
            "score": round(0.92 - i * 0.05, 2),
        }
        for i, (doc_id, title, content) in enumerate(sources)
    ]


def _node_entry(
    agent: str,
    name: str,
    started_at: str,
    t0: float,
    retry_index: int,
    output_summary: str,
    prompt: str | None = None,
) -> dict[str, Any]:
    """11.2 nodes[] 单条（结构与迁移前逐字一致）。"""
    entry = {
        "agent": agent,
        "name": name,
        "startedAt": started_at,
        "endedAt": _now_iso(),
        "durationMs": round((time.perf_counter() - t0) * 1000, 2),
        "retryIndex": retry_index,
        "outputSummary": output_summary,
    }
    if prompt is not None:
        entry["promptExcerpt"] = prompt[:PROMPT_EXCERPT_LEN]
    return entry


# ── State 读取小工具 ───────────────────────────────────────────────────────────


def _ctx(state: MultiAgentState) -> dict[str, Any]:
    return dict(state.get("learning_context") or {})


def _execution(state: MultiAgentState) -> dict[str, Any]:
    return dict(state.get("execution") or {})


def _task_id(state: MultiAgentState, stage: str) -> str:
    identity = state.get("identity") or {}
    root = str(identity.get("workflowId") or "").strip() or "wf_local"
    return f"{root}:{stage}"


def _append_log(state: MultiAgentState, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """nodeLog 是嵌套列表 → 整体替换为「已有的 + 新增」（见模块文档纪律 2）。"""
    existing: list[dict[str, Any]] = (state.get("execution") or {}).get("nodeLog") or []
    return [*existing, entry]


# ── 节点 ──────────────────────────────────────────────────────────────────────


# ── R4.4：Tool Loop 取证增强（AGENT_TOOL_LOOP_ENABLED，默认关闭）───────────────
#
# **设计原则（R4.4 §5 结果兼容）**：Tool Loop 只负责**取证**，
# 真正的 markdown / passed / validationScore / hallucinationRate / issues
# **仍然由既有 `run_generator` / `run_critic` 产生** ——
# 因此工具接入不改变输出结构、不改 State 十字段、不改 11.2、
# 不改 retry / degraded / 阈值 / RAG grounding 算法。
#
# 开关 OFF → **原样返回，零改动**，连 trace 都不产生（保证与接入前逐字一致）。

_TOOL_LOOP_TRACE_LIMIT = 200          # 进程内 trace 上限（防无界增长）
_MAX_EXTRA_CHUNKS = 6                 # 单次最多并入的可信切片数（防 prompt 膨胀）
_MAX_NOTES = 3                        # 单次最多并入的非 RAG 工具摘要条数
_NOTE_LEN = 200                       # 单条摘要截断长度
_OBSERVATION_LEN = 800                # trace 中 observation 序列化截断长度

_tool_loop_trace: list[dict[str, Any]] = []


def tool_loop_trace() -> list[dict[str, Any]]:
    """本进程最近的 Tool Loop 内部 trace（**不进 11.2**，仅供观测/测试）。"""
    return list(_tool_loop_trace)


def clear_tool_loop_trace() -> None:
    """清空内部 trace（仅供测试）。"""
    _tool_loop_trace.clear()


def _record_tool_loop(entry: dict[str, Any]) -> None:
    _tool_loop_trace.append(entry)
    while len(_tool_loop_trace) > _TOOL_LOOP_TRACE_LIMIT:
        _tool_loop_trace.pop(0)


def _truncate_json(payload: Any) -> str | None:
    """把 observation 序列化并截断（trace 里存不下完整画像/知识库返回）。"""
    if payload is None:
        return None
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text if len(text) <= _OBSERVATION_LEN else text[:_OBSERVATION_LEN] + "…"


def _split_observations(result: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """从 ToolLoopResult 历史里分离**可信 RAG 切片**与**其余工具的可读摘要**。

    - ``trusted_rag`` 的 ``chunks`` 是真·检索证据 → 并入 ``rag_context``；
    - 画像/掌握度/路径等是结构化 JSON → 不进 grounding（避免污染逐句接地计算），
      改为文本摘要并入 ``learner_profile``。
    """
    chunks: list[dict[str, Any]] = []
    notes: list[str] = []
    for item in getattr(result, "history", None) or []:
        if item.get("kind") != "observation" or item.get("error"):
            continue
        try:
            data = json.loads(item.get("content") or "{}")
        except Exception:  # noqa: BLE001 — 观察内容不可解析就跳过，不打断流程
            continue
        if not isinstance(data, dict):
            continue
        tool = str(item.get("tool") or "")
        if tool == RAG_TOOL_NAME:
            for chunk in data.get("chunks") or []:
                if isinstance(chunk, dict) and chunk.get("content"):
                    chunks.append(chunk)
        else:
            notes.append(
                f"{tool}: "
                + json.dumps(data, ensure_ascii=False, default=str)[:_NOTE_LEN]
            )
    return chunks[:_MAX_EXTRA_CHUNKS], notes[:_MAX_NOTES]


def _tool_loop_enrichment(
    *,
    state: MultiAgentState,
    agent_id: str,
    task: str,
    base_rag: list[dict[str, Any]],
    base_profile: str = "",
) -> tuple[list[dict[str, Any]], str, dict[str, Any] | None]:
    """按 feature flag 跑 Tool Loop，把证据并入**既有**生成/审核入参。

    Args:
        state: 当前 MultiAgentState（只读 ``identity.userId`` 作 actor_id）。
        agent_id: ``generation`` / ``critic`` —— 权限取自 ``BaseAgent.tools``。
        task: 交给决策源的任务描述。
        base_rag: 原始 ``evidence.ragContext``（**不改写，只追加**）。
        base_profile: 原始 ``learner_profile``（仅 generation 使用）。

    Returns:
        ``(rag_context, learner_profile, trace_entry)`` ——
        任何失败路径都返回**原始入参**，即**自动回退旧路径**；
        flag 关闭时 ``trace_entry`` 为 ``None``（不产生任何痕迹）。

    三层失败策略（R4.4 §6）：
        1. Tool Loop 正常 → 用取得的证据继续；
        2. ``truncated`` → **仍用已有可交付证据继续**，在 trace 里标记；
        3. 运行时异常 / actor_id 缺失 → 记 error + ``fallback="old_path"``，
           **完全不使用工具结果**，退回原入参。
    """
    if not settings.agent_tool_loop_enabled:
        return base_rag, base_profile, None

    actor_id = str((state.get("identity") or {}).get("userId") or "").strip()
    if not actor_id:
        entry = {
            "agent": agent_id,
            "enabled": True,
            "error": "missing actor_id",
            "fallback": "old_path",
        }
        _record_tool_loop(entry)
        logger.warning("tool_loop_skip agent=%s reason=missing_actor_id", agent_id)
        return base_rag, base_profile, entry

    try:
        result = ToolLoop(agent_id=agent_id, actor_id=actor_id).run(task)
    except Exception as exc:  # noqa: BLE001 — 工具循环故障绝不能拖垮工作流
        entry = {
            "agent": agent_id,
            "enabled": True,
            "error": f"{type(exc).__name__}: {exc}",
            "fallback": "old_path",
        }
        _record_tool_loop(entry)
        logger.warning(
            "tool_loop_failed agent=%s error_type=%s → 回退旧路径",
            agent_id,
            type(exc).__name__,
        )
        return base_rag, base_profile, entry

    extra_chunks, notes = _split_observations(result)
    base_ids = {chunk.get("id") for chunk in base_rag}
    rag = [*base_rag]
    rag.extend(chunk for chunk in extra_chunks if chunk.get("id") not in base_ids)

    profile = base_profile
    if notes:
        block = "【工具补充证据】\n" + "\n".join(notes)
        profile = f"{base_profile}\n{block}" if base_profile else block

    final_observation = result.final_observation or {}
    entry = {
        "agent": agent_id,
        "enabled": True,
        "toolSteps": result.tool_steps,
        "toolCalls": result.tool_calls,
        "lastTool": result.last_tool,
        "truncated": result.truncated,
        "degraded": bool(final_observation.get("degraded")),
        "fallbackReason": result.fallback_reason,
        "error": result.error,
        "extraChunks": len(extra_chunks),
        "notes": len(notes),
        "steps": [
            {
                "step": step.step,
                "tool": step.tool,
                "arguments": step.arguments,
                "success": step.success,
                "degraded": step.degraded,
                "error": step.error,
                "durationMs": step.duration_ms,
                "observation": _truncate_json(step.result),
            }
            for step in result.steps
        ],
    }
    _record_tool_loop(entry)
    logger.info(
        "tool_loop_trace agent=%s toolSteps=%s toolCalls=%s truncated=%s "
        "degraded=%s fallback=%s error=%s",
        entry["agent"],
        entry["toolSteps"],
        entry["toolCalls"],
        entry["truncated"],
        entry["degraded"],
        entry["fallbackReason"],
        entry["error"],
    )
    return rag, profile, entry


def build_graph(
    db: Session,
    retriever: Retriever,
    disable_critic: bool = False,
    checkpointer: Any = None,
):
    """装配资源生成子图并编译。

    Args:
        db: 数据库会话（**闭包捕获，绝不进 State**）。
        retriever: 检索 callable；闭包注入，**绝不进 State**。
        disable_critic: 仅供 B11 消融实验脚本使用（业务路径恒 False）。
        checkpointer: LangGraph 断点存储；``None``（默认）= 与迁移前
            ``graph.compile()`` 完全等价，运行语义零变化。

    Returns:
        编译后的 LangGraph 图。
    """

    def diagnostic_node(state: MultiAgentState) -> dict[str, Any]:
        started, t0 = _now_iso(), time.perf_counter()
        identity = state.get("identity") or {}
        learning = _ctx(state)
        user_id = identity.get("userId") or ""
        # 读当前用户真实画像 + 掌握度 → 因人而异的学情诊断（不写死通用模板）。
        # user_id 缺省（极端兜底）时退化为空画像 / 空掌握度，仍可跑通。
        portrait = (
            portrait_service.get_portrait(db, user_id) if user_id else {"dimensions": []}
        )
        status_map = mastery_service.get_status_map(db, user_id) if user_id else {}
        kp_name = learning.get("kpName", "")
        res = run_diagnostic(
            db,
            kp_id=learning.get("kpId", ""),
            kp_name=kp_name,
            profile_summary=_portrait_summary(portrait),
            mastery_status=json.dumps(status_map, ensure_ascii=False),
            target_job=learning.get("targetJob", ""),
        )
        summary = res["output"]["summary"]
        task_id = _task_id(state, "diagnostic")
        return {
            "learning_context": {"profile": res["output"]},
            "execution": {
                "currentNode": "diagnostic",
                "status": "running",
                "nodeLog": _append_log(
                    state,
                    _node_entry("diagnosis", res["name"], started, t0, 0, summary, res["prompt"]),
                ),
            },
            "messages": [
                make_message(
                    task_id=task_id,
                    agent_name=res["name"],
                    display_from="用户",
                    display_to=res["name"],
                    display_message=f"发起「{kp_name}」学习资源生成请求",
                    display_type="request",
                    inputs={"kpId": learning.get("kpId", ""), "kpName": kp_name},
                    outputs={"summary": summary},
                ),
                make_message(
                    task_id=task_id,
                    agent_name=res["name"],
                    display_from=res["name"],
                    display_to="领域知识生成Agent",
                    display_message=summary,
                    outputs={"weakKpIds": res["output"].get("weakKpIds", [])},
                ),
            ],
        }

    def retrieval_node(state: MultiAgentState) -> dict[str, Any]:
        started, t0 = _now_iso(), time.perf_counter()
        learning = _ctx(state)
        chunks = retriever(
            learning.get("kpId", ""),
            learning.get("kpName", ""),
            learning.get("difficulty", ""),
        )
        summary = f"检索到 {len(chunks)} 条相关知识切片"
        return {
            "evidence": {"ragContext": chunks},
            "execution": {
                "currentNode": "retrieval",
                "nodeLog": _append_log(
                    state, _node_entry("rag", "RAG系统", started, t0, 0, summary)
                ),
            },
            "messages": [
                make_message(
                    task_id=_task_id(state, "retrieval"),
                    agent_name="RAG系统",
                    display_from="RAG系统",
                    display_to="领域知识生成Agent",
                    display_message=summary,
                    outputs={"chunkCount": len(chunks)},
                )
            ],
        }

    def generation_node(state: MultiAgentState) -> dict[str, Any]:
        started, t0 = _now_iso(), time.perf_counter()
        identity = state.get("identity") or {}
        learning = _ctx(state)
        execution = _execution(state)
        artifacts = state.get("artifacts") or {}
        retry_index = int(execution.get("retryCount", 0))
        # 读当前用户画像 → 派生讲义深度档，并把画像文本注入生成 prompt。
        # 空画像 → tier=None 落难度基线（不强加个性化）。
        user_id = identity.get("userId") or ""
        portrait = (
            portrait_service.get_portrait(db, user_id) if user_id else {"dimensions": []}
        )
        learner_profile = (
            _portrait_summary(portrait)
            if (portrait.get("dimensions") or [])
            else "画像尚未采集（按通用基线）"
        )
        kp_id, kp_name = learning.get("kpId", ""), learning.get("kpName", "")
        # R4.4：flag 开启 → 先经 Tool Loop 取证，再把证据并入既有入参；
        # flag 关闭 / 工具异常 → 返回原样入参（**完全等价于旧路径**）。
        # 第三个返回值（trace entry）已由 _tool_loop_enrichment 内部记入
        # tool_loop_trace()，不进 11.2（见 §7 Trace 兼容性）。
        rag_context, learner_profile, _ = _tool_loop_enrichment(
            state=state,
            agent_id="generation",
            task=(
                f"为知识点「{kp_name}」（难度 {learning.get('difficulty', '')}）生成讲义，"
                "请先检索该生的学习记忆、画像、掌握度与可信资料作为证据"
            ),
            base_rag=(state.get("evidence") or {}).get("ragContext") or [],
            base_profile=learner_profile,
        )
        res = run_generator(
            db,
            kp_name=kp_name,
            difficulty=learning.get("difficulty", ""),
            rag_context=rag_context,
            feedback=artifacts.get("feedback"),
            description=learning.get("kpDescription", ""),
            learner_profile=learner_profile,
            depth_tier=_ability_tier(portrait, kp_id, kp_name),
        )
        markdown = res["output"]["markdown"]
        nth = f"第 {retry_index + 1} 次" if retry_index else "首次"
        summary = f"讲义草稿生成完成（{nth}，{len(markdown)} 字符）"
        return {
            "artifacts": {"markdown": markdown},
            "execution": {
                "currentNode": "generation",
                "nodeLog": _append_log(
                    state,
                    _node_entry(
                        "generation", res["name"], started, t0, retry_index, summary, res["prompt"]
                    ),
                ),
            },
            "messages": [
                make_message(
                    task_id=_task_id(state, "generation"),
                    agent_name=res["name"],
                    display_from=res["name"],
                    display_to="内容审核校验Agent",
                    display_message=f"{summary}，请校验",
                    display_type="request",
                    outputs={"markdownLength": len(markdown)},
                )
            ],
        }

    def validation_node(state: MultiAgentState) -> dict[str, Any]:
        started, t0 = _now_iso(), time.perf_counter()
        execution = _execution(state)
        plan = state.get("plan") or {}
        artifacts = state.get("artifacts") or {}
        evidence = state.get("evidence") or {}
        retry_count = int(execution.get("retryCount", 0))
        max_retries = int(plan.get("maxRetries", MAX_RETRIES))

        if disable_critic:
            # 消融「去审核」：不做接地校验，直接接受首版生成（无重试、无降级）。
            summary = "审核已禁用（消融实验），首版生成直接交付"
            return {
                "quality": {
                    "result": {
                        "passed": True, "validationScore": None,
                        "hallucinationRate": None, "issues": [], "action": "accept",
                    },
                    "hallucinationRate": None,
                },
                "execution": {
                    "currentNode": "validation",
                    "nodeLog": _append_log(
                        state,
                        _node_entry("critic", "内容审核校验Agent", started, t0, retry_count, summary),
                    ),
                },
                "messages": [
                    make_message(
                        task_id=_task_id(state, "validation"),
                        agent_name="内容审核校验Agent",
                        display_from="内容审核校验Agent",
                        display_to="领域知识生成Agent",
                        display_message=summary,
                    )
                ],
            }

        # R4.4：flag 开启 → 先取证，把**可信检索切片**并入审核对照依据；
        # 画像/掌握度等结构化结果**刻意不进 grounding 输入**（避免污染逐句接地计算）。
        # flag 关闭 / 工具异常 → 原样入参，完全等价于旧路径。
        rag_context, _, _ = _tool_loop_enrichment(
            state=state,
            agent_id="critic",
            task=(
                f"审核讲义草稿「{(artifacts.get('markdown') or '')[:80]}…」前，"
                "请检索该生的掌握度、学习记忆与可信资料作为对照依据"
            ),
            base_rag=evidence.get("ragContext") or [],
        )
        res = run_critic(
            db,
            draft_content=artifacts.get("markdown") or "",
            rag_context=rag_context,
        )
        out = res["output"]
        score = out["validationScore"]
        passed = out["passed"] and score >= VALIDATION_THRESHOLD

        update: dict[str, Any] = {
            "quality": {
                "result": {**out, "passed": passed},
                "hallucinationRate": out["hallucinationRate"],
            },
            "execution": {"currentNode": "validation"},
        }

        if passed:
            update["quality"]["result"]["action"] = "accept"
            summary = f"校验通过（评分 {score}，幻觉率 {out['hallucinationRate']}）"
            update["messages"] = [
                make_message(
                    task_id=_task_id(state, "validation"),
                    agent_name=res["name"],
                    display_from=res["name"],
                    display_to="领域知识生成Agent",
                    display_message=summary,
                    outputs={"validationScore": score, "hallucinationRate": out["hallucinationRate"]},
                )
            ]
        elif retry_count < max_retries:
            update["quality"]["result"]["action"] = "retry"
            # 手动 +1 后写回（迁移前语义）；节点日志仍记**递增前**的 retryCount
            update["execution"]["retryCount"] = retry_count + 1
            update["artifacts"] = {
                "feedback": "\n".join(out.get("issues") or ["校验未通过"])
            }
            summary = (
                f"校验未通过（评分 {score} < 阈值 {VALIDATION_THRESHOLD}），"
                f"发起第 {retry_count + 1} 次重试"
            )
            update["messages"] = [
                make_message(
                    task_id=_task_id(state, "validation"),
                    agent_name=res["name"],
                    display_from=res["name"],
                    display_to="领域知识生成Agent",
                    display_message=summary,
                    display_type="error",
                    outputs={"validationScore": score, "issues": out.get("issues") or []},
                )
            ]
            update["errors"] = [
                {"retryIndex": retry_count, "score": score, "issues": out.get("issues") or []}
            ]
        else:
            update["quality"]["result"]["action"] = "fallback"
            summary = (
                f"校验仍未通过（评分 {score}），已达最大重试次数 {max_retries}，触发降级输出"
            )
            update["messages"] = [
                make_message(
                    task_id=_task_id(state, "validation"),
                    agent_name=res["name"],
                    display_from=res["name"],
                    display_to="用户",
                    display_message=summary,
                    display_type="error",
                    outputs={"validationScore": score},
                )
            ]
            update["errors"] = [
                {"retryIndex": retry_count, "score": score, "issues": out.get("issues") or []}
            ]

        # 合并 execution：currentNode 在上，nodeLog 追加（nodeLog 必须整体替换）
        execution_update = dict(update["execution"])
        execution_update["nodeLog"] = _append_log(
            state,
            _node_entry("critic", res["name"], started, t0, retry_count, summary, res["prompt"]),
        )
        update["execution"] = execution_update
        return update

    def decision_node(state: MultiAgentState) -> dict[str, Any]:
        quality = state.get("quality") or {}
        artifacts = state.get("artifacts") or {}
        execution = _execution(state)
        validation = quality.get("result") or {}
        degraded = not validation.get("passed", False)
        result = {
            "markdown": artifacts.get("markdown") or "",
            "validationScore": validation.get("validationScore"),
            "hallucinationRate": quality.get("hallucinationRate"),
            "degraded": degraded,
            "retryCount": int(execution.get("retryCount", 0)),
        }
        if degraded:
            # 降级策略（需求文档 4.2.2）：交付最后一稿并标记待人工复核
            result["requiresReview"] = True
            message_text = (
                f"已降级交付（validationScore={validation.get('validationScore')}，待人工复核）"
            )
        else:
            message_text = "校验通过，学习资源已交付"
        return {
            "result": result,
            "execution": {
                "currentNode": "decision",
                "status": "degraded" if degraded else "success",
            },
            "messages": [
                make_message(
                    task_id=_task_id(state, "decision"),
                    agent_name="内容审核校验Agent",
                    display_from="内容审核校验Agent",
                    display_to="用户",
                    display_message=message_text,
                    outputs={"degraded": degraded},
                )
            ],
        }

    def route_after_validation(state: MultiAgentState) -> str:
        """条件路由（需求文档 4.2.1 should_retry）：决策在 validation 节点落定。"""
        quality = state.get("quality") or {}
        return (quality.get("result") or {}).get("action", "accept")

    graph = StateGraph(MultiAgentState)
    graph.add_node("diagnostic", diagnostic_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("generation", generation_node)
    graph.add_node("validation", validation_node)
    graph.add_node("decision", decision_node)

    graph.add_edge("diagnostic", "retrieval")
    graph.add_edge("retrieval", "generation")
    graph.add_edge("generation", "validation")
    graph.add_conditional_edges(
        "validation",
        route_after_validation,
        {"retry": "generation", "accept": "decision", "fallback": "decision"},
    )
    graph.add_edge("decision", END)
    # R4.5 依赖核对：原为 graph.set_entry_point("diagnostic") —— 该方法在 langgraph 1.x
    # 已废弃（语义等价于 START 边），是全仓**唯一**一处版本敏感 API。
    # 改用 add_edge(START, ...)：0.2 → 1.x 全版本稳定，行为逐字等价。
    graph.add_edge(START, "diagnostic")
    return graph.compile(checkpointer=checkpointer)


# 便于诊断/测试引用节点签名（非图入口）
__all__ = [
    "MAX_RETRIES",
    "VALIDATION_THRESHOLD",
    "Retriever",
    "build_graph",
    "_ability_tier",
    "_mock_retriever",
    "_node_entry",
    "_portrait_summary",
]
