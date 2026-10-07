"""ORM 模型（B1）。

字段命名严格对齐《后端接口文档》第 2 章数据字典与第 14 章管理端模型：
- 对外 JSON 用 camelCase（如 lessonSeq / graphNodeId / questionType），
  列名在此用 snake_case，schema 层做映射，避免 Python 关键字/风格冲突。
- 本阶段（B1）只负责建表与种子，业务读写在 B2+ 落地。

覆盖 10 张表：User / KnowledgePoint / Lesson / Mastery / Journey /
ResourceCache / QuizQuestion / KnowledgeDocument / PromptTemplate / JobSnapshot。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AsyncTaskRecord(Base):
    """异步任务持久化快照；对外仍使用既有 Task 契约。"""

    __tablename__ = "async_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    task_type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    scope_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class User(Base):
    """用户（含 role 双角色）。对应登录响应 user 对象（接口文档 3.1 / 15.1）。"""

    __tablename__ = "users"

    # 对外 user.userId；用字符串 ID（如 u_10001）便于契约对齐
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="learner")  # learner | admin
    # B9：账号启停（禁用 → 登录受阻，接口文档 16.0/16.4）；最近活跃 = 最近登录时间（16.2）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class KnowledgePoint(Base):
    """知识点（接口文档 2.1）。

    原「固定 6 核心知识点」扩充为《AI知识体系设计-v3》78 点 / 7 板块完整体系（催化剂
    会话·录入）：**只录结构与元信息，内容由生成引擎按需产出**。新增列全部为**追加、
    向后兼容**（既有 6 核心行经迁移默认落 is_core=1、保留其已生成内容与测试基准）：

    - code：体系编码（如 ML-1 / DL-6 / GEN-4），全 78 点唯一；6 核心行映射到体系点
      （ml→ML-1 / nn→DL-1 / dl→DL-4 / cnn→DL-6 / transformer→LLM-2 / finetune→LLM-5）。
    - category：所属板块码（ML/DL/CV/LLM/GEN/AGT/RLX，共 7 板块）。
    - level：层级（入门|进阶|前沿）。
    - prerequisites：先修知识点 **id 列表**（种子时由体系 code 解析为 id，跨板块交叉亦然）。
    - is_core：是否为「已验证基准」6 核心点（有题库/资源/测试）。诊断微测、能力雷达、
      学习路径、掌握度覆盖率等既有链路一律**按 is_core 收窄**到这 6 点（不因扩充回归）；
      其余 72 点为体系目录（供知识图谱浏览 + 先修推断），不进既有 6 点闭环。
    """

    __tablename__ = "knowledge_points"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # 如 nn / ML-2
    name: Mapped[str] = mapped_column(String(64))
    lesson_seq: Mapped[int] = mapped_column(Integer)  # 对外 lessonSeq（体系目录点取高位序，排在 6 核心之后）
    graph_node_id: Mapped[str] = mapped_column(String(32))  # 对外 graphNodeId
    description: Mapped[str] = mapped_column(Text, default="")  # 供 8.1 知识点元信息
    # ── 体系元信息（追加列，向后兼容；见类 docstring） ──
    code: Mapped[str] = mapped_column(String(16), default="")  # 体系编码 ML-1 等
    category: Mapped[str] = mapped_column(String(16), default="")  # 板块码 ML/DL/CV/LLM/GEN/AGT/RLX
    level: Mapped[str] = mapped_column(String(16), default="")  # 入门|进阶|前沿
    prerequisites: Mapped[list] = mapped_column(JSON, default=list)  # 先修 kp id 列表
    is_core: Mapped[bool] = mapped_column(Boolean, default=True)  # 6 已验证基准点=True


class Lesson(Base):
    """学习路径课程（接口文档 2.3）。B1 种子 6 课 sequence 1-6。"""

    __tablename__ = "lessons"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)  # 1-6
    topic: Mapped[str] = mapped_column(String(128))
    difficulty: Mapped[str] = mapped_column(String(16))  # 入门|初级|中级|高级|精通
    status: Mapped[str] = mapped_column(String(16))  # completed|in_progress|pending
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    description: Mapped[str] = mapped_column(Text, default="")


class Mastery(Base):
    """掌握度（接口文档 2.2 / 7.1，Record<kpId,KPStatus> 行存）。"""

    __tablename__ = "mastery"
    __table_args__ = (UniqueConstraint("user_id", "kp_id", name="uq_mastery_user_kp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16))  # learning|pending-check|passed
    # C2 能力口径统一：能力分(0-100)单一来源——诊断微测写低置信基线、真实 quiz 写高置信，
    # 同一行覆盖更新；4.4 能力雷达读此分。三列均可空（向后兼容旧行，未测=None）。
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100，None=未测
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-1
    score_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # diagnostic|quiz


class UserKnowledgeStateRecord(Base):
    """节点级知识状态快照（TASK-005-B，内部模型，不改变 Mastery 契约）。"""

    __tablename__ = "user_knowledge_states"
    __table_args__ = (
        UniqueConstraint("user_id", "knowledge_id", name="uq_knowledge_state_user_node"),
        CheckConstraint(
            "mastery_score >= 0 AND mastery_score <= 1",
            name="ck_knowledge_state_mastery_score",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_knowledge_state_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    knowledge_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("knowledge_points.id"), index=True
    )
    mastery_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class LearningEventRecord(Base):
    """知识状态更新的追加式反馈历史（TASK-005-B）。"""

    __tablename__ = "learning_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_learning_event_event_id"),
        CheckConstraint("score >= 0 AND score <= 1", name="ck_learning_event_score"),
        CheckConstraint(
            "event_type IN ('quiz','practice','feynman','diagnostic','retrieval',"
            "'learning_step','self_report')",
            name="ck_learning_event_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    knowledge_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("knowledge_points.id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(128))
    algorithm_version: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)


class LearningEventAnomalyRecord(Base):
    """TASK-005-D Shadow 旁路异常审计，不参与线上 Mastery 或路径计算。"""

    __tablename__ = "learning_event_anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    knowledge_id: Mapped[str] = mapped_column(String(32), index=True)
    anomaly_type: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


class Journey(Base):
    """学习旅程（接口文档 7.4）。currentStep 由 B2 按规则推导，不入库。"""

    __tablename__ = "journeys"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id"), primary_key=True
    )
    has_diagnosed: Mapped[bool] = mapped_column(Boolean, default=False)
    has_generated_path: Mapped[bool] = mapped_column(Boolean, default=False)
    target_job_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    match_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 真实规划 Agent（接口文档 6.2）产出的该用户个性化路径快照：Lesson[] + 每步
    # reason/resources（additive 字段，见接口文档 6.3 增量）。NULL = 尚未生成 → GET
    # 回落全局种子路径（向后兼容）。首次生成后落库，GET 命中缓存（同一画像不重复规划）。
    path_plan: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # C2：缓存路径对应的画像/掌握度指纹（planner.portrait_fingerprint）。GET 时若当前
    # 指纹与此不符 → 画像已变 → 实时重算个性化路径（不缓存写死）。NULL = 未缓存。
    path_fingerprint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # C2：整体规划叙述（"为你这样规划的理由"），随 path_plan 一并缓存，GET 回 summary.narrative。
    path_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResourceCache(Base):
    """资源生成缓存（接口文档 8.2 讲义产物等）。B2+ 写入。"""

    __tablename__ = "resource_cache"
    __table_args__ = (
        UniqueConstraint("kp_id", "difficulty", "kind", name="uq_res_kp_diff_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    difficulty: Mapped[str] = mapped_column(String(16), default="")
    kind: Mapped[str] = mapped_column(String(32), default="lecture")  # lecture|video|...
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 完整响应体
    hallucination_rate: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class QuizQuestion(Base):
    """测验题（接口文档 2.5）。B2 种子题库。"""

    __tablename__ = "quiz_questions"

    question_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    question_type: Mapped[str] = mapped_column(String(16))  # single|multiple|boolean
    question_text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)  # [{option_id,option_text}]
    correct_answer: Mapped[object] = mapped_column(JSON)  # string 或 string[]
    explanation: Mapped[str] = mapped_column(Text, default="")


class KnowledgeDocument(Base):
    """知识库文档（接口文档 14.1）。B3 灌库回填 chunks/status。"""

    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # doc_001
    title: Mapped[str] = mapped_column(String(256))
    filename: Mapped[str] = mapped_column(String(256))
    size: Mapped[int] = mapped_column(Integer, default=0)  # 字节
    category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|indexing|indexed|failed
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PromptTemplate(Base):
    """Agent Prompt 模板（接口文档 14.5，热更新）。agentId 固定 3 项。"""

    __tablename__ = "prompt_templates"

    agent_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # diagnosis|generation|critic
    name: Mapped[str] = mapped_column(String(64))
    template: Mapped[str] = mapped_column(Text)
    variables: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class WorkflowTrace(Base):
    """工作流执行轨迹（B5-a）。结构满足接口文档 11.2 agents/messages 渲染，B7 直接消费。

    - agents / messages：11.2 响应 data 的同名数组（驱动 AgentStatusCard / 消息日志）；
    - node_log：每节点 {agent,name,startedAt,endedAt,durationMs,retryIndex,outputSummary,promptExcerpt}；
    - rag_context_used：本次生成实际使用的检索切片（B5-a 为确定性 mock，B5-b 接真实检索）。
    """

    __tablename__ = "workflow_traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # wf_xxx
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kp_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # success|degraded|failed
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hallucination_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    agents: Mapped[list] = mapped_column(JSON, default=list)  # 11.2 agents[]
    messages: Mapped[list] = mapped_column(JSON, default=list)  # 11.2 messages[]
    node_log: Mapped[list] = mapped_column(JSON, default=list)
    rag_context_used: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StudentPortrait(Base):
    """异质学生动态画像（接口文档 17.2 / 17.3，C1-b）。

    每用户一行，dimensions 存 PortraitDimension[]（contract camelCase 形态：
    key/label/value/score?/confidence/source/updatedAt）。由对话式诊断（17.1）
    增量构建、随学习/测验更新（随学随新）；与 ability-portrait（4.4 固定 6 知识点
    雷达）并存、互不替换。空画像 dimensions=[]（17.3「尚未开始诊断」占位）。
    """

    __tablename__ = "student_portraits"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id"), primary_key=True
    )
    dimensions: Mapped[list] = mapped_column(JSON, default=list)  # PortraitDimension[]
    version: Mapped[str] = mapped_column(String(16), default="v1")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class JobSnapshot(Base):
    """岗位市场快照（接口文档 2.4 / 15.5）。payload 存完整 JobMarket 结构。

    B6：种子从 frontend/public/data/job-market/*.json 导入（demo 阶段后端托管
    预置快照即数据源）；生产路径为离线采集管线定期刷新本表（写 README，不在 demo 实现）。
    """

    __tablename__ = "job_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # llm-app
    name: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)  # 热榜顺序（5.1 契约示例序）
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ExternalResource(Base):
    """外部精选资源（接口文档 8.6，B6 种子精选库）。

    demo 阶段为人工精选种子（每核心知识点 3-4 条，relevance/credibility 为
    审核 Agent 评分口径的预置值）；生产路径：聚合 Agent 调 YouTube Data API /
    B 站 / arXiv / 慕课等搜索 API 检索候选 → critic Agent 按相关性 + 来源可信度
    评分过滤 → 写回本表（接口签名不变，写 README，不在 demo 实现）。
    """

    __tablename__ = "external_resources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # nn-r1
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    type: Mapped[str] = mapped_column(String(8))  # 视频|论文|文档|课程
    title: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(512))
    embed: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 可嵌入播放地址
    duration: Mapped[str | None] = mapped_column(String(32), nullable=True)
    relevance: Mapped[int] = mapped_column(Integer, default=0)  # 相关度 0-100
    credibility: Mapped[int] = mapped_column(Integer, default=0)  # 可信度 0-100
    reason: Mapped[str] = mapped_column(Text, default="")


class ExternalResourceCache(Base):
    """联网资源聚合结果缓存；保留真实 URL 与评分，联网失败时可降级复用。"""

    __tablename__ = "external_resource_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    query: Mapped[str] = mapped_column(String(512))
    items_json: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class LearningStepProgress(Base):
    """学习步骤进度（接口文档 18.3，C2）。单用户单知识点下各过程步骤完成标记。

    步骤集固定 6 项：video|lecture|diagram|note|feynman|practice（视频/讲义/图解/
    笔记/费曼/实操）。与 Mastery（2.2 三态）**解耦**——Mastery 粒度不足以表达 6 步
    过程；「已掌握」仍由阶段测试（9.1）驱动 Mastery（7.3），本表只记过程完成标记。
    **无外键耦合**（user_id 仅作隔离键，不建外键），不改 Journey/Mastery 表（契约 18.6）。
    """

    __tablename__ = "learning_step_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "kp_id", "step", name="uq_step_user_kp_step"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    step: Mapped[str] = mapped_column(String(16))  # video|lecture|diagram|note|feynman|practice
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class LearningNote(Base):
    """康奈尔笔记（接口文档 18.4/18.5，C2）。单用户单知识点一份三栏笔记。

    cue_notes（线索区作答 [{cueId,text}]）、main_notes（主笔记区 Markdown）、
    summary（总结区）。轻量持久化（随学随新、跨设备不丢，契约 18.6 建议），
    **无外键耦合**，不改 Journey/Mastery 表。
    """

    __tablename__ = "learning_notes"
    __table_args__ = (UniqueConstraint("user_id", "kp_id", name="uq_note_user_kp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    cue_notes: Mapped[list] = mapped_column(JSON, default=list)  # [{cueId,text}]
    main_notes: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class GenerationLog(Base):
    """用户资源生成历史埋点（「我的资源库」数据层）。

    每当用户生成/获取一类学习资源（讲义/视频/图解/思维导图/代码），追加一条**带
    user_id 归属**的记录——区别于无用户归属的 ResourceCache 全局缓存（后者按
    (kp_id,difficulty,kind) 唯一、供全体复用，无 user_id）。本表回答「这个用户
    为哪些知识点、在什么难度下、生成过哪些形态的资源」，驱动「我的资源库」横向资产视图。

    埋点是**追加式旁路**，不改既有生成逻辑：生成成功后调 generation_log.record() 追加。
    去重口径：同一 (user_id,kp_id,kind,difficulty) 视为同一份资产，重复生成/再次获取
    仅**刷新 created_at（最近生成时间）**（唯一约束 + upsert），避免反复浏览刷成历史噪声。
    kind 存**归一化基类**（lecture/video/diagram/mindmap/code），剥离 ResourceCache 里
    的 @provider / #tier 后缀，保证一种资产一行、与 provider/画像档无关。
    **无外键耦合**（user_id/kp_id 仅作隔离/分组键），不改既有表结构（契约「新增追加」）。
    """

    __tablename__ = "generation_logs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "kp_id", "kind", "difficulty", name="uq_genlog_user_kp_kind_diff"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # lecture|video|diagram|mindmap|code
    difficulty: Mapped[str] = mapped_column(String(16), default="")  # 图解/导图/代码无难度→""
    title: Mapped[str] = mapped_column(String(256), default="")  # 展示标题（知识点名 · 形态）
    # 定位/查看引用（`kind:kp_id:difficulty`）；前端亦可由 kpId+kind+difficulty 复原查看/下载
    resource_ref: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    # 「文档学习」平行链路埋点（新增，向后兼容）：内置课程资源 source=NULL/"builtin"，
    # 文档学习资源 source="document" + doc_id/doc_title 携带文档来源标识；用于「我的资源库」
    # 区分展示与筛选。built-in 既有行三列均为 NULL，behavior 不变。
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # builtin|document
    doc_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 文档来源 id
    doc_title: Mapped[str | None] = mapped_column(String(256), nullable=True)  # 文档标题（展示）
    # 产物落库（新增，向后兼容）：把生成产物的**实际内容**随资产行一并持久化，让「查看」
    # 直接读已存产物渲染、**不再调用生成接口重跑 RAG+大模型**（消除 ~25s 等待与内容不一致、
    # 部署后重复烧 API）。文本类产物（讲义 markdown / 图解 mermaid / 思维导图 / 练习题 /
    # 闪卡 / 视频分镜脚本）存本列 JSON；视频 mp4 等大文件仍走文件系统 + URL（artifact 内存
    # videoUrl 引用）。内置课程资源 artifact=NULL（其产物由无用户归属的 ResourceCache 全局
    # 缓存承载、命中即读、本就 0 重复生成）；文档学习资源落本列。「重新生成」显式覆盖本列 +
    # 刷新 artifact_updated_at/created_at。artifact=NULL 表示尚未物化（旧行/内置行）。
    artifact: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 产物实际内容（完整响应体）
    artifact_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 产物最近落库/刷新时间


class Document(Base):
    """用户上传文档（「文档学习」平行链路，users.id 隔离键，无外键耦合）。

    与内置知识库 KnowledgeDocument（14.1，管理端、无 user 归属）**完全分开**——本表
    是学习者上传的私有文档，每篇绑定一个**专属向量集合** collection（Chroma 命名空间
    隔离），与内置课程知识库 kb_chunks 互不污染。解析后的纯文本存 content（供无向量
    降级/摘要复用）；status 状态机 pending|indexing|indexed|failed（异步入库回填）。
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # udoc_xxx
    user_id: Mapped[str] = mapped_column(String(64), index=True)  # 无 FK（隔离键）
    title: Mapped[str] = mapped_column(String(256))
    filename: Mapped[str] = mapped_column(String(256))
    file_type: Mapped[str] = mapped_column(String(16), default="")  # pdf|md|txt|docx
    size: Mapped[int] = mapped_column(Integer, default=0)  # 字节
    # 该文档专属向量集合名（Chroma collection / 降级库命名空间）——隔离核心。
    collection: Mapped[str] = mapped_column(String(64), default="")
    content: Mapped[str] = mapped_column(Text, default="")  # 解析后的纯文本（摘要/降级用）
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|indexing|indexed|failed
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(256), nullable=True)  # 失败原因
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


class UserModelConfig(Base):
    """用户自建生成模型配置（接口文档 21.3+，模型管理独立页）。

    每行 = 某用户界面添加的一个 OpenAI 兼容模型接入（魔搭 / deepseek / 其他 openai
    兼容端点）。**按 user 隔离**：user_id 为隔离键（无外键耦合，随既有约定），任何
    读写/使用均校验归属。api_key 经 app/core/crypto.py Fernet **加密落库**（本表无
    明文 key）；api_key_last4 仅存后四位供界面脱敏回显（****abcd）。
    """

    __tablename__ = "user_model_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # umc_xxx
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(64))  # 界面显示名
    provider: Mapped[str] = mapped_column(String(16))  # openai|modelscope|deepseek（均 OpenAI 兼容）
    base_url: Mapped[str] = mapped_column(String(256))
    model_id: Mapped[str] = mapped_column(String(128))  # 上游 API 的 model 参数
    api_key_encrypted: Mapped[str] = mapped_column(Text)  # Fernet token，非明文
    api_key_last4: Mapped[str] = mapped_column(String(8), default="")  # 脱敏回显用
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class UserModelChoice(Base):
    """用户「当前模型」选择（接口文档 21.2 per-user 语义扩展）。

    仅当用户把**自建配置**设为当前时落一行（model_key = umc_xxx）——该选择只影响
    本人后续生成（A 的 key 绝不会被 B 用到）。切回内置模型时删除本行（内置模型
    切换维持既有进程级运行态语义，向后兼容 §21.2）。
    """

    __tablename__ = "user_model_choices"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(128))  # 自建配置 id（umc_xxx）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class QuizAttempt(Base):
    """测验作答历史埋点（C-fix 批3：学习过程评估行为数据层）。

    每次 9.1 提交落一行（轻量埋点），供「学习评估 Agent」聚合做题表现/趋势/效率。
    与既有判分链路解耦——只记录结果快照，不改 quiz/Mastery 判分逻辑；**无外键耦合**
    （user_id/kp_id 仅作隔离/分组键），不改既有表结构。
    """

    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kp_id: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)  # 综合得分 0-100
    correct_count: Mapped[int] = mapped_column(Integer, default=0)  # 客观答对数
    total: Mapped[int] = mapped_column(Integer, default=0)  # 总题数
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
