"""个性化学习路径规划 Agent（赛题功能 3，接口文档 6.2 真实化）。

输入 = 真实 StudentPortrait（6 维异质画像）+ 真实 Mastery（知识掌握度）+ 学习目标/
目标岗位（Journey.target_job_name / 请求 targetJobId → JobSnapshot.radar 需求）；
输出 = **科学、有先修顺序的个性化路径**：每步明确「学什么、什么顺序、为什么这样排」，
并整合该知识点系统已生成的资源（讲义/思维导图/图解/视频/题库/外部精选）。

设计要点：
- **排序确定性、可解释、可测**：优先级打分在本 Agent 内（不依赖随机/网络），
  保证「无密钥 mock 也能跑通且不同画像/掌握度生成不同路径」；理由叙述层经
  LLMClient.plan_path（mock 模板 / deepseek 真实生成，见 core.llm）。
- **薄弱点优先、已掌握后置**：未掌握(weak)不加惩罚 → 前置；passed 大幅后移 →
  路径末段复习。先修依赖以 KnowledgePoint.lesson_seq 为骨架（ml→nn→dl→cnn→
  transformer→finetune），避免「把 Transformer 排在神经网络之前」。
- **画像联动**：knowledge_base 基础分低 → 难度下调一档、加固基础；高 → 难度上调
  一档、基础课可略读后置。目标岗位需求高的知识点在满足先修后前置强化。
- **资源精准推送**：每步 resources 指向该 kpId 真实存在的生成资源端点（能点开）。

复用既有服务：student_portrait / mastery / job_market / resource，不重建。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import PATH_DIFFICULTIES, get_llm
from app.models.entities import JobSnapshot, Journey, KnowledgePoint, Lesson
from app.services import knowledge_catalog
from app.services import mastery as mastery_service
from app.services import student_portrait as portrait_service

# 微测/quiz 能力分达「达标」阈值：未正式通过测验但能力分 ≥ 此值 → 视为已达标、后置略读
# （与「能力靠测」一致：用实测分而非仅 passed 状态判定"学什么/什么顺序"）。
_PROFICIENT_SCORE: int = 70

# 掌握状态 → 路径步骤展示状态/进度：统一走 mastery_service.learning_step（诊断基线视为
# 未开始、仅真实通过测验才"已完成"），不再静态映射。

# KP → 岗位雷达能力维名（core.llm.ABILITY_DIMENSIONS 口径）：用于读 JobSnapshot.radar
# 的该知识点岗位需求值（cnn 无独立能力维，回落「深度学习」需求）。
_KP_TO_ABILITY: dict[str, str] = {
    "ml": "机器学习基础",
    "nn": "神经网络",
    "dl": "深度学习",
    "cnn": "深度学习",
    "transformer": "Transformer",
    "finetune": "大模型微调",
}

# 每步推送的系统已生成资源（按 kpId 真实存在，端点对齐接口文档 8.x/9.1，可点开）。
# (kind, 标题模板, 端点, 是否带难度参数)
_RESOURCE_SPECS: list[tuple[str, str, str, bool]] = [
    ("lecture", "《{topic}》自适应讲义", "/resource/lecture", True),
    ("mindmap", "{topic} · 思维导图", "/resource/mindmap", False),
    ("diagram", "{topic} · 知识图解", "/resource/diagram", False),
    ("video", "{topic} · 讲解视频", "/resource/video", True),
    ("quiz", "{topic} · 巩固测验", "/quiz", False),
    ("external", "{topic} · 精选外部资源", "/resource/external", False),
]

# 资源页讲义/视频难度档（入门|初级|高级）；路径难度档更细（含中级/精通）需映射回 8.2 档
_LECTURE_DIFF_FALLBACK = "初级"

# ── 偏好画像 → 每个节点「默认推荐的资源形式」（C2：偏好决定"每一步怎么学"） ──
# 认知风格首选形式（图像型→图解、文字型→详细讲义、案例型→外部实战/案例）
_STYLE_PRIMARY: dict[str, str] = {
    "visual": "diagram",
    "textual": "lecture",
    "example": "external",
}
# 学习节奏次选形式（快速概览型→先思维导图鸟瞰、稳步细钻型→详细讲义逐点钻）
_PACE_PRIMARY: dict[str, str] = {
    "overview": "mindmap",
    "deepdive": "lecture",
}
# 资源形式中文名（用于推荐理由）
_KIND_LABEL: dict[str, str] = {
    "lecture": "自适应讲义",
    "mindmap": "思维导图",
    "diagram": "知识图解",
    "video": "讲解视频",
    "quiz": "巩固测验",
    "external": "精选外部资源",
}
_STYLE_LABEL: dict[str, str] = {"visual": "图像型", "textual": "文字型", "example": "案例型"}
_PACE_LABEL: dict[str, str] = {"overview": "快速概览型", "deepdive": "稳步细钻型"}

# ── 时间线（会话三·新增）：每个知识点「预计时长」按层级 level 主导 + 展示难度微调估算 ──
# 层级基线（分钟）：入门较短、进阶居中、前沿较长（题面「入门较短、前沿较长」）。
_LEVEL_BASE_MIN: dict[str, int] = {"入门": 60, "进阶": 120, "前沿": 180}
# 展示难度（5 档，随基础分自适应）微调：难度越高多花些时间，反之略减。
_DIFF_DELTA_MIN: dict[str, int] = {"入门": -15, "初级": 0, "中级": 15, "高级": 30, "精通": 45}
_MIN_FLOOR, _MIN_CEIL = 45, 240  # 单点时长约束在 45 分钟 ~ 4 小时，四舍五入到 15 分钟
# 目录点（无种子 Lesson 难度）→ 由层级映射基础难度档，再经 _adapt_difficulty 自适应。
_LEVEL_TO_DIFF: dict[str, str] = {"入门": "入门", "进阶": "中级", "前沿": "高级"}
_LEVEL_RANK: dict[str, int] = {"入门": 0, "进阶": 1, "前沿": 2}

# ── 目标/岗位 → 聚焦板块（会话三·在 78 点树上按目标裁剪出合理长度路径） ──
# 学习目标 / 目标岗位文本命中关键词 → 该板块细化点纳入规划（无命中 → 回落 6 核心主干）。
_BOARD_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("LLM", ("大模型", "llm", "语言模型", "nlp", "自然语言", "gpt", "chatgpt", "文本", "prompt", "提示词", "rag")),
    ("GEN", ("生成式", "生成模型", "扩散", "diffusion", "aigc", "文生图", "绘画", "stable diffusion", "sora", "图像生成")),
    ("AGT", ("智能体", "agent", "多智能体", "langchain", "工作流编排", "autogpt", "autogen")),
    ("CV", ("计算机视觉", "视觉", "图像识别", "目标检测", "图像分割", "opencv", "yolo")),
    ("RLX", ("强化学习", "reinforcement", "决策", "博弈", "机器人控制")),
    ("DL", ("深度学习", "神经网络", "deep learning", "cnn", "rnn", "卷积")),
    ("ML", ("机器学习", "数据分析", "数据挖掘", "特征工程", "回归", "聚类", "数据科学")),
]
_MAX_PATH_STEPS = 12  # 路径合理长度上限（6 核心主干 + 目标板块细化点，题面示例约 12 点）

# 板块 → 雷达能力维度映射（用于岗位需求兜底选板）
_BOARD_ABILITIES: dict[str, tuple[str, ...]] = {
    "LLM": ("Transformer", "大模型微调"),
    "CV":  ("神经网络", "深度学习"),
    "DL":  ("深度学习", "神经网络"),
    "ML":  ("机器学习基础",),
    "AGT": ("Transformer",),
    "GEN": ("深度学习",),
    "RLX": ("机器学习基础",),
}


def _foundation_score(dims: list[dict[str, Any]]) -> int | None:
    """从画像维度取 knowledge_base 基础分（0-100）；缺失返回 None（中性）。"""
    for d in dims:
        if d.get("key") == "knowledge_base":
            score = d.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                return int(score)
    return None


def _dim_value(dims: list[dict[str, Any]], key: str) -> str:
    for d in dims:
        if d.get("key") == key:
            return str(d.get("value") or "")
    return ""


def _dim_option(dims: list[dict[str, Any]], key: str) -> str:
    """取偏好维的类型码 optionKey（如 cognitive_style→visual）；缺失返回 ""。"""
    for d in dims:
        if d.get("key") == key:
            return str(d.get("optionKey") or "")
    return ""


def _foundation_label(score: int | None) -> str:
    if score is None:
        return "中等（未量化）"
    if score >= 75:
        return "扎实"
    if score < 40:
        return "薄弱"
    return "一般"


def _lecture_difficulty(path_difficulty: str) -> str:
    """路径难度档（5 档）→ 资源讲义/视频难度档（入门|初级|高级，接口文档 8.2）。"""
    mapping = {
        "入门": "入门",
        "初级": "初级",
        "中级": "初级",
        "高级": "高级",
        "精通": "高级",
    }
    return mapping.get(path_difficulty, _LECTURE_DIFF_FALLBACK)


def _adapt_difficulty(base: str, foundation: int | None) -> str:
    """按画像基础分上下浮动一档（接口文档 2.3 难度阶梯）。"""
    try:
        idx = PATH_DIFFICULTIES.index(base)
    except ValueError:
        idx = 1
    if foundation is not None:
        if foundation >= 75:
            idx = min(len(PATH_DIFFICULTIES) - 1, idx + 1)  # 基础扎实 → 拔高
        elif foundation < 40:
            idx = max(0, idx - 1)  # 零基础 → 降档加固
    return PATH_DIFFICULTIES[idx]


def _job_demand(db: Session, job_id: str | None, job_name: str | None) -> dict[str, int]:
    """读目标岗位 6 能力维需求（接口文档 2.4 radar）。无岗位 → 空（不加权）。"""
    snap: JobSnapshot | None = None
    if job_id:
        snap = db.get(JobSnapshot, job_id)
    if snap is None and job_name:
        snap = (
            db.query(JobSnapshot).filter(JobSnapshot.name == job_name).one_or_none()
        )
    if snap is None:
        return {}
    radar = (snap.payload or {}).get("radar") or {}
    return {k: int(v) for k, v in radar.items() if isinstance(v, (int, float))}


def _preferred_kind_order(style: str, pace: str) -> list[str]:
    """据偏好画像排「资源形式优先级」：认知风格首选 + 学习节奏次选 + 其余默认序，去重。"""
    order: list[str] = []
    primary = _STYLE_PRIMARY.get(style)
    if primary:
        order.append(primary)
    secondary = _PACE_PRIMARY.get(pace)
    if secondary and secondary not in order:
        order.append(secondary)
    for kind, _, _, _ in _RESOURCE_SPECS:  # 其余形式按默认序补齐
        if kind not in order:
            order.append(kind)
    return order


def _recommend_reason(kind: str, style: str, pace: str) -> str:
    """生成"为你默认推荐这种形式"的可解释理由（扣住认知风格/学习节奏）。"""
    label = _KIND_LABEL.get(kind, kind)
    if style in _STYLE_LABEL and _STYLE_PRIMARY.get(style) == kind:
        return f"你是{_STYLE_LABEL[style]}学习者，优先推「{label}」建立直观理解。"
    if pace in _PACE_LABEL and _PACE_PRIMARY.get(pace) == kind:
        tip = "先鸟瞰全局再补细节" if pace == "overview" else "逐点稳稳钻透"
        return f"你偏{_PACE_LABEL[pace]}，优先用「{label}」{tip}。"
    return f"默认从「{label}」入手。"


def _build_resources(
    kp_id: str, topic: str, path_difficulty: str, style: str = "", pace: str = ""
) -> list[dict[str, Any]]:
    """每步推送的资源清单：指向该 kpId 真实存在的生成资源端点（能点开）。

    C2：资源**顺序按偏好画像重排**（认知风格 + 学习节奏），首项标 `recommended=true`
    并附 `recommendReason`——同一知识点，图像型默认推图解、文字/细钻型推详细讲义、
    概览型先推思维导图，使"每一步怎么学"因人而异。无偏好 → 默认序、不标推荐。
    """
    lecture_diff = _lecture_difficulty(path_difficulty)
    specs_by_kind = {kind: spec for spec in _RESOURCE_SPECS for kind in (spec[0],)}
    kind_order = _preferred_kind_order(style, pace) if (style or pace) else [s[0] for s in _RESOURCE_SPECS]
    has_pref = bool(style or pace)
    resources: list[dict[str, Any]] = []
    for idx, kind in enumerate(kind_order):
        spec = specs_by_kind.get(kind)
        if spec is None:
            continue
        _, title_tpl, endpoint, with_diff = spec
        item: dict[str, Any] = {
            "kind": kind,
            "title": title_tpl.format(topic=topic),
            "kpId": kp_id,
            "endpoint": endpoint,
        }
        if with_diff:
            item["difficulty"] = lecture_diff
        if has_pref and idx == 0:  # 首项 = 按偏好默认推荐的形式
            item["recommended"] = True
            item["recommendReason"] = _recommend_reason(kind, style, pace)
        resources.append(item)
    return resources


# ── 会话三：在 78 点知识树上按目标聚焦裁剪 + 时间线估算 ──────────────────────────

def _focus_boards(goal_text: str, job_name: str, job_demand: dict[str, int] | None = None) -> list[str]:
    """据学习目标 + 目标岗位文本匹配聚焦板块（最多 2，去重保序）。

    文本无命中 → 根据岗位需求雷达图选需求最高的板块（确保路径因岗位而异）；
    均无 → 回落 DL+ML（AI 通用基础）。
    """
    hay = f"{goal_text} {job_name}".lower()
    boards: list[str] = []
    for board, kws in _BOARD_KEYWORDS:
        if board in boards:
            continue
        if any(kw.lower() in hay for kw in kws):
            boards.append(board)
        if len(boards) >= 2:
            break
    # 文本关键词无命中 → 用岗位需求雷达图选板块（哪个维度分高就侧重哪个方向）
    if not boards and job_demand:
        scored = []
        for board, abilities in _BOARD_ABILITIES.items():
            total = sum(job_demand.get(a, 0) for a in abilities)
            if total > 0:
                scored.append((board, total))
        scored.sort(key=lambda x: -x[1])
        boards = [b for b, _ in scored[:2]]
    # 最终兜底：AI 两大基础板块
    if not boards:
        boards = ["DL", "ML"]
    return boards


def _prereq_closure(seed_ids: set[str], all_kp: dict[str, Any]) -> set[str]:
    """先修传递闭包：使选中的知识点在路径中可学（补齐其先修）。纯图遍历、确定性。"""
    closed = set(seed_ids)
    stack = list(seed_ids)
    while stack:
        cur = stack.pop()
        kp = all_kp.get(cur)
        for pre in (list(kp.prerequisites or []) if kp is not None else []):
            if pre in all_kp and pre not in closed:
                closed.add(pre)
                stack.append(pre)
    return closed


def _select_kps(
    db: Session, focus_boards: list[str], core: list[KnowledgePoint]
) -> list[KnowledgePoint]:
    """选出参与规划的知识点集合（会话三）。

    - 有聚焦 → 6 核心 + 目标板块细化点 + 其先修闭包，按「聚焦优先 / 层级由浅入深 /
      先修序」排序后裁剪到 `_MAX_PATH_STEPS`，得到合理长度、可学的个性化路径；
    - 无聚焦（_focus_boards 兜底 DL+ML）→ 同上逻辑，扩展点来自深度学习+机器学习板块。
    排序骨架仍交由 plan_path 的能力打分（本函数只决定"纳入哪些点"，不决定最终顺序）。
    """
    if not focus_boards:
        return core
    core_ids = {kp.id for kp in core}
    all_rows = db.query(KnowledgePoint).all()
    all_kp: dict[str, Any] = {kp.id: kp for kp in all_rows}
    in_focus = {kp.id for kp in all_rows if kp.category in focus_boards}
    closure = _prereq_closure(core_ids | in_focus, all_kp)
    extension = [i for i in closure if i not in core_ids]

    def _rank(kp_id: str) -> tuple[int, int, int]:
        kp = all_kp[kp_id]
        return (
            0 if kp_id in in_focus else 1,          # 目标板块点优先于跨板块补进的先修点
            _LEVEL_RANK.get(kp.level or "", 1),     # 由浅入深（入门→进阶→前沿）
            kp.lesson_seq,                          # 先修骨架兜底
        )

    extension.sort(key=_rank)
    keep = extension[: max(0, _MAX_PATH_STEPS - len(core))]
    keep_set = set(keep)
    return list(core) + [all_kp[i] for i in extension if i in keep_set]


def estimate_minutes(level: str | None, difficulty: str | None) -> int:
    """单个知识点「预计时长」（分钟）：层级基线 + 展示难度微调，约束 45m~4h、取整 15m。

    合理估算规则（非真实计时）：入门约 1 小时、进阶约 2 小时、前沿约 3 小时，随展示
    难度（随基础分自适应的 5 档）小幅上下浮动，使「入门较短、前沿较长」且因画像而略变。
    """
    base = _LEVEL_BASE_MIN.get(level or "", 120)
    delta = _DIFF_DELTA_MIN.get(difficulty or "", 0)
    raw = max(_MIN_FLOOR, min(_MIN_CEIL, base + delta))
    return int(round(raw / 15) * 15)


def build_timeline(lessons: list[dict[str, Any]], pace: str = "") -> dict[str, Any]:
    """整条路径「时间线」聚合（会话三·additive）：周期/总时长/进度/节奏建议。

    - totalCount/learnedCount：知识点总数 / 已学（真实通过 completed）数；
    - totalMinutes/totalHours：预计总时长；spentMinutes：已花时长（completed 计满、
      in_progress 按进度折算）；completionPct：时长加权完成度；
    - pacePerDay/cycleDays/cycleWeeks：按学习节奏偏好（快速概览 2/天，其余 1/天）推周期；
    - pacing：学习节奏文案（如「建议每天 1-2 个知识点，预计 2 周学完」）。
    """
    import math

    total_count = len(lessons)
    total_min = sum(int(ls.get("estimatedMinutes") or 0) for ls in lessons)
    learned = sum(1 for ls in lessons if ls.get("status") == "completed")
    spent = int(round(sum(
        int(ls.get("estimatedMinutes") or 0) * (int(ls.get("progress") or 0) / 100)
        for ls in lessons
    )))
    completion = round(spent / total_min * 100) if total_min else 0
    per_day = 2 if pace == "overview" else 1
    cycle_days = max(1, math.ceil(total_count / per_day)) if total_count else 0
    cycle_weeks = max(1, math.ceil(cycle_days / 7)) if cycle_days else 0
    per_label = "1-2" if per_day >= 2 else "1"
    pacing = (
        f"建议每天 {per_label} 个知识点，预计 {cycle_weeks} 周（约 {cycle_days} 天）学完。"
        if total_count else "完成学情诊断后即可生成个性化学习节奏建议。"
    )
    return {
        "totalCount": total_count,
        "learnedCount": learned,
        "totalMinutes": total_min,
        "totalHours": round(total_min / 60, 1),
        "spentMinutes": spent,
        "completionPct": completion,
        "pacePerDay": per_day,
        "cycleDays": cycle_days,
        "cycleWeeks": cycle_weeks,
        "pacing": pacing,
    }


def plan_path(
    db: Session, *, user_id: str, target_job_id: str | None = None, narrate: bool = True
) -> dict[str, Any]:
    """生成该用户的个性化学习路径（接口文档 6.2，真实规划）。

    Args:
        narrate: True=理由经 LLMClient（mock 模板 / deepseek 真实，POST generate 用）；
            False=强制确定性模板、零网络（GET 实时重算用——画像变即重算、不阻塞、不计费）。

    Returns:
        {lessons, summary}：
        - lessons：Lesson[]（接口文档 2.3 六字段）+ additive kpId/reason/resources，
          sequence 1..N 为**真实学习顺序**（按 per-KP 能力薄弱优先/先修依赖/岗位需求重排）；
        - summary：整体规划说明（驱动前端路径头部叙述）。
    """
    # ---- 1. 真实输入源（全部复用既有服务，不重建） ----
    dims = list(portrait_service.get_portrait(db, user_id).get("dimensions") or [])
    foundation = _foundation_score(dims)
    # C2：偏好画像 → 决定"每一步怎么学"（认知风格/学习节奏的类型码）
    style = _dim_option(dims, "cognitive_style")
    pace = _dim_option(dims, "learning_pace")
    status_map = mastery_service.get_status_map(db, user_id)
    # C2：per-KP 能力分（微测/quiz 写入 Mastery）→ 决定"学什么、什么顺序"
    score_map = mastery_service.get_score_map(db, user_id)
    journey = db.get(Journey, user_id)
    job_name = journey.target_job_name if journey else None
    demand = _job_demand(db, target_job_id, job_name)

    # 会话三：在 78 点知识树上按「学习目标/目标岗位」聚焦板块细化规划——
    # 6 核心主干 + 目标板块细化点（先修闭包、裁剪到合理长度）；无目标 → 仅 6 核心（零回归）。
    core = sorted(knowledge_catalog.core_kps(db), key=lambda k: k.lesson_seq)
    focus = _focus_boards(_dim_value(dims, "learning_goal"), job_name or "", demand)
    focus_set = set(focus)
    kps = _select_kps(db, focus, core)
    # 基础难度/描述取自全局种子课程（按 lesson_seq == Lesson.sequence 对应）
    lessons_by_seq = {ls.sequence: ls for ls in db.query(Lesson).all()}

    # ---- 2. 优先级打分（lower = 越早学；科学排程，靠测的 per-KP 能力分驱动） ----
    scored: list[dict[str, Any]] = []
    for kp in kps:
        status = status_map.get(kp.id)  # None = 未开始
        mastered = status == mastery_service.STATUS_PASSED
        kp_score = (score_map.get(kp.id) or {}).get("score")  # int | None（None=未测）
        tested = mastered or kp_score is not None
        # 「达标」= 已通过测验 或 微测/quiz 能力分 ≥ 阈值（靠测，不只看 status）
        proficient = mastered or (kp_score is not None and kp_score >= _PROFICIENT_SCORE)
        weak = not proficient  # 未达标 = 薄弱/待强化
        foundational = kp.lesson_seq <= 2  # ml/nn 为后续基础
        ability = _KP_TO_ABILITY.get(kp.id, "")
        job_boost = bool(weak and demand.get(ability, 0) >= 80)

        score = float(kp.lesson_seq)  # 先修骨架（保证依赖顺序）
        # 目标板块 KP 大幅前置：折扣使其能排到核心 KP 前面（lesson_seq 1000 → ~5）
        if kp.category in focus_set:
            score = score * 0.005
        if proficient:
            # 已掌握/微测已达标 → 大幅后置（路径末段复习/略读，"跳过已会的"）
            score += 100.0
        else:
            # 真薄弱：**能力分越低越前**（未测=0=最薄弱，优先补）；fractional 不破坏先修整数骨架
            eff = kp_score if kp_score is not None else 0
            score += eff / 100.0 * 0.8
            # 画像基础联动：强基础者基础课略后、零基础先固基础
            if foundational and foundation is not None:
                if foundation >= 75:
                    score += 0.4
                elif foundation < 40:
                    score -= 0.4
            # 目标岗位高需求 → 满足先修后小幅前置
            if ability and demand:
                score -= min(0.8, demand.get(ability, 0) / 100.0 * 0.8)

        scored.append(
            {
                "kp": kp,
                "status": status,
                # 能力分来源（quiz/diagnostic/None）：用于区分"真实学习"与"仅诊断基线"
                "source": (score_map.get(kp.id) or {}).get("source"),
                "score": score,
                "signals": {
                    "weak": weak,
                    "mastered": mastered,
                    "proficient": proficient and not mastered,  # 微测达标但未正式通过
                    "foundational": foundational,
                    "jobBoost": job_boost,
                    "abilityScore": kp_score,
                    "tested": tested,
                },
            }
        )

    # 稳定排序：分数升序，先修序兜底（同分保持课程骨架顺序）
    scored.sort(key=lambda e: (e["score"], e["kp"].lesson_seq))

    # ---- 3. 组装步骤（含难度自适应、状态/进度派生、资源推送） ----
    steps_ctx: list[dict[str, Any]] = []
    lessons: list[dict[str, Any]] = []
    for order, entry in enumerate(scored, start=1):
        kp: KnowledgePoint = entry["kp"]
        seed = lessons_by_seq.get(kp.lesson_seq)
        # 6 核心取种子 Lesson 难度；目录细化点（无种子）按层级映射基础难度档。
        base_diff = seed.difficulty if seed is not None else _LEVEL_TO_DIFF.get(kp.level or "", "初级")
        difficulty = _adapt_difficulty(base_diff, foundation)
        # 「已完成」只来自真实通过测验（passed）；诊断微测基线视为未开始（微测≠学完）
        status, progress = mastery_service.learning_step(entry["status"], entry["source"])
        description = (seed.description if seed is not None else "") or kp.description

        lesson: dict[str, Any] = {
            "sequence": order,
            "topic": kp.name,
            "difficulty": difficulty,
            "status": status,
            "progress": progress,
            "description": description,
            # ---- additive（接口文档 6.3 增量，向后兼容） ----
            "kpId": kp.id,
            # 会话三·时间线：该步预计时长（分钟，按层级/难度估算）
            "estimatedMinutes": estimate_minutes(kp.level, difficulty),
            "reason": "",  # 由 LLMClient.plan_path 回填
            # 资源形式按偏好画像（认知风格/学习节奏）重排，首项为默认推荐（C2）
            "resources": _build_resources(kp.id, kp.name, difficulty, style, pace),
        }
        lessons.append(lesson)
        steps_ctx.append(
            {
                "kpId": kp.id,
                "topic": kp.name,
                "order": order,
                "status": entry["status"] or "not-started",
                "signals": entry["signals"],
            }
        )

    # ---- 4. 理由 + 摘要叙述层（经 LLMClient：mock 模板 / deepseek 真实） ----
    profile = {
        "foundationLevel": _foundation_label(foundation),
        "goal": _dim_value(dims, "learning_goal"),
        "pace": _dim_value(dims, "learning_pace"),
        "jobName": job_name or "",
        # C2 偏好画像（驱动摘要里"怎么学"的叙述）
        "style": _STYLE_LABEL.get(style, ""),
        "paceType": _PACE_LABEL.get(pace, ""),
    }
    annotated = get_llm().plan_path(profile=profile, steps=steps_ctx, deterministic=not narrate)
    reasons = annotated.get("reasons") or {}
    for lesson in lessons:
        lesson["reason"] = reasons.get(lesson["kpId"], "")

    return {"lessons": lessons, "summary": annotated.get("summary", "")}


def portrait_fingerprint(db: Session, user_id: str) -> str:
    """画像/掌握度指纹：用于判定缓存路径是否过期（画像变 → 指纹变 → 重算）。

    覆盖一切影响排程/资源的输入：画像维度（含能力分/偏好 optionKey）、per-KP 能力分、
    掌握状态、目标岗位。任一变化 → 指纹变化 → GET 实时重算个性化路径（不缓存写死）。
    """
    import hashlib
    import json

    dims = sorted(
        (
            (d.get("key"), d.get("score"), d.get("optionKey"), d.get("value"))
            for d in (portrait_service.get_portrait(db, user_id).get("dimensions") or [])
        ),
        key=lambda t: str(t[0]),
    )
    score_map = mastery_service.get_score_map(db, user_id)
    status_map = mastery_service.get_status_map(db, user_id)
    journey = db.get(Journey, user_id)
    payload = {
        "dims": dims,
        "scores": sorted((k, v.get("score")) for k, v in score_map.items()),
        "status": sorted(status_map.items()),
        "job": journey.target_job_name if journey else None,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
