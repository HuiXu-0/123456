"""学习资源服务（B2-b mock 数据源 / B5-b 真实生成替换）。

覆盖接口文档第 8 章前两个接口：
- 8.1 knowledge_point_meta：知识点元信息 { id, name, description, status }，
  status 取自掌握度（未开始默认 learning，与前端资源页初始一致）。
- 8.2 generate_lecture：自适应讲义 markdown + sources + hallucinationRate；
  生成讲义同时把该知识点置为 learning。
  - mock：经 LLMClient.generate_lecture 确定性产出（与 B2 逐字等价，演示兜底）；
  - 真实模式（B5-b）：run_learning_workflow「B3 混合检索+重排 → 生成 Agent →
    审核 Agent（15.3 接地口径）」，sources 由真实命中切片回填
    （document_title/source_location → title，文档 category → type，
    重排分 → confidence），hallucinationRate 为逐句接地计算值。

讲义结果写入 ResourceCache（按 kp+difficulty+kind 唯一），命中则直接返回缓存，
体现「同档不重复再生成」；真实模式 kind 带 provider 后缀（lecture@deepseek），
与 mock 缓存互不污染——切换 provider 后演示数据不串。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core import generation_provenance

from app.core import lecture_media
from app.core.llm import LECTURE_DIFFICULTIES, get_llm
from app.models.entities import (
    ExternalResource,
    KnowledgeDocument,
    KnowledgePoint,
    ResourceCache,
)
from app.rag.reranker import get_reranker
from app.rag.retriever import get_retriever
from app.services import mastery as mastery_service
from app.services import student_portrait as portrait_service
from app.workflows.learning_workflow import _ability_tier, run_learning_workflow

# 接口文档 8.2 sources[].type 合法取值；文档 category 不在其中时回落「文档」
_SOURCE_TYPES = {"教材", "论文", "文档", "课程"}
# 讲义检索参数：候选池与最终注入生成的切片数。
# B8 调优 4→6：生成模板要求覆盖知识点全部核心概念，top-4 切片常覆盖不全，
# 模型被迫凭自身知识补写 → 未接地句；放宽到 top-6 让概念在上下文内闭环。
_RETRIEVE_POOL = 12
_RETRIEVE_TOP_K = 6


class UnknownKnowledgePoint(Exception):
    """知识点不存在（→ code 1004 / 404）。"""


class InvalidDifficulty(Exception):
    """难度档非法（→ code 1001 / 400）。"""


def _require_kp(db: Session, kp_id: str) -> KnowledgePoint:
    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise UnknownKnowledgePoint(kp_id)
    return kp


def knowledge_point_meta(db: Session, user_id: str, kp_id: str) -> dict[str, Any]:
    """知识点元信息（接口文档 8.1）。status 取自掌握度，未开始默认 learning。"""
    kp = _require_kp(db, kp_id)
    status = mastery_service.get_status_map(db, user_id).get(
        kp_id, mastery_service.STATUS_LEARNING
    )
    return {
        "id": kp.id,
        "name": kp.name,
        "description": kp.description,
        "status": status,
    }


def external_resources(db: Session, kp_id: str) -> list[dict[str, Any]]:
    """外部资源聚合（接口文档 8.6，B6）。按相关度降序返回精选资源种子。

    demo 阶段读 ExternalResource 精选库（init_db 种子，6 核心 KP 各 3-4 条，
    relevance/credibility 为审核 Agent 评分口径预置值）；生产路径：聚合 Agent
    调 YouTube Data API / B 站 / arXiv / 慕课等搜索 API 检索候选 → critic Agent
    按相关性 + 来源可信度评分过滤 → 写回 ExternalResource 表，本函数与接口签名不变。
    embed/duration 为可选字段，仅在有值时返回（契约 8.6）。
    """
    _require_kp(db, kp_id)
    rows = (
        db.query(ExternalResource)
        .filter(ExternalResource.kp_id == kp_id)
        .order_by(ExternalResource.relevance.desc(), ExternalResource.id)
        .all()
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {
            "id": r.id,
            "type": r.type,
            "title": r.title,
            "source": r.source,
            "url": r.url,
            "relevance": r.relevance,
            "credibility": r.credibility,
            "reason": r.reason,
        }
        if r.embed:
            item["embed"] = r.embed
        if r.duration:
            item["duration"] = r.duration
        items.append(item)
    return items


# ---- 思维导图 / Mermaid 图解（接口文档 8.4 / 8.5，B8 补齐） --------------------
# 确定性内容（无 LLM 成本，不走缓存）：nn 与前端 LearningResource.tsx 本地常量
# （mindmapMarkdown / mermaidChart）逐字对齐；其余 5 个核心知识点按相同结构
# 领域定制。生产路径：由生成 Agent 按讲义大纲实时产出（接口签名不变）。
_MINDMAPS: dict[str, str] = {
    "nn": (
        "# 神经网络基础\n"
        "## 神经元\n"
        "### 加权求和 Σ w·x\n"
        "### 加偏置 +b\n"
        "### 激活函数\n"
        "#### ReLU\n"
        "#### Sigmoid\n"
        "#### Tanh\n"
        "## 前向传播\n"
        "### 输入层 → 隐藏层 → 输出层\n"
        "## 反向传播\n"
        "### 计算梯度\n"
        "### 梯度下降\n"
        "### 学习率 η\n"
        "## 进阶方向\n"
        "### CNN\n"
        "### RNN\n"
        "### Transformer\n"
    ),
    "ml": (
        "# 机器学习基础\n"
        "## 监督学习\n"
        "### 分类\n"
        "### 回归\n"
        "## 无监督学习\n"
        "### 聚类\n"
        "### 降维\n"
        "## 特征与损失\n"
        "### 特征工程\n"
        "### 损失函数\n"
        "## 过拟合与正则\n"
        "### L1 / L2 正则\n"
        "### 早停\n"
        "### 交叉验证\n"
    ),
    "dl": (
        "# 深度学习原理\n"
        "## 反向传播\n"
        "### 链式法则\n"
        "### 计算图\n"
        "## 梯度下降与优化器\n"
        "### SGD\n"
        "### Adam\n"
        "### RMSprop\n"
        "## 正则与归一化\n"
        "### Dropout\n"
        "### BatchNorm\n"
        "## 训练要点\n"
        "### 学习率调度\n"
        "### 梯度消失 / 爆炸\n"
    ),
    "cnn": (
        "# CNN架构\n"
        "## 卷积\n"
        "### 卷积核\n"
        "### 权重共享\n"
        "### 步长与填充\n"
        "## 池化\n"
        "### 最大池化\n"
        "### 平均池化\n"
        "## 感受野\n"
        "## 经典网络\n"
        "### LeNet\n"
        "### AlexNet\n"
        "### ResNet\n"
    ),
    "transformer": (
        "# Transformer架构\n"
        "## 自注意力\n"
        "### Query / Key / Value\n"
        "### 缩放点积\n"
        "## 多头注意力\n"
        "### 并行子空间\n"
        "## 位置编码\n"
        "### 正弦位置编码\n"
        "## 整体结构\n"
        "### 编码器\n"
        "### 解码器\n"
        "### 残差与 LayerNorm\n"
    ),
    "finetune": (
        "# 大模型微调技术\n"
        "## 全参微调\n"
        "### 全量参数更新\n"
        "### 算力开销大\n"
        "## 参数高效微调\n"
        "### LoRA 低秩分解\n"
        "### Adapter\n"
        "## 指令微调\n"
        "### SFT 监督微调\n"
        "### 指令数据构造\n"
        "## 对齐\n"
        "### RLHF\n"
        "### DPO\n"
    ),
}


def mindmap(db: Session, kp_id: str) -> dict[str, Any]:
    """思维导图（接口文档 8.4）。返回 Markmap 语法 Markdown 大纲。"""
    kp = _require_kp(db, kp_id)
    markdown = _MINDMAPS.get(kp_id) or (
        f"# {kp.name}\n## 核心概念\n### {kp.description}\n## 实践应用\n## 进阶方向\n"
    )
    generation_provenance.mark_deterministic()
    return {"markdown": markdown}


def diagram(db: Session, kp_id: str) -> dict[str, Any]:
    """Mermaid 知识图解（接口文档 8.5）。经 LLMClient 按当前知识点真实生成。

    与讲义/视频同口径走 get_llm()：
    - mock：确定性主题流程图（已收录知识点逐字模板，nn 与前端一致）；
    - deepseek：真实生成 + 契约清洗（失败回落主题模板，图解始终可渲染）。

    结果写 ResourceCache（kind=diagram / diagram@<provider>，知识图解与难度无关故
    difficulty 固定空串），命中直接返回，避免真实模式重复触发 LLM 调用。
    """
    kp = _require_kp(db, kp_id)
    llm = get_llm()
    kind = "diagram" if llm.is_mock else f"diagram@{llm.provider}"
    cached = (
        db.query(ResourceCache)
        .filter(
            ResourceCache.kp_id == kp_id,
            ResourceCache.difficulty == "",
            ResourceCache.kind == kind,
        )
        .one_or_none()
    )
    if cached is not None:
        generation_provenance.mark_cache()
        return dict(cached.payload)

    payload = llm.generate_diagram(kp.id, kp.name, kp.description or "")
    db.add(
        ResourceCache(
            kp_id=kp_id,
            difficulty="",
            kind=kind,
            payload=payload,
            hallucination_rate=0.0,
        )
    )
    db.commit()
    return payload


# ---- 视频讲解（接口文档 8.3，B7-a；分镜脚本随知识点动态生成） ------------------
# 视频参数与前端 Remotion 组件逐一对齐（frontend/src/remotion/LectureVideo.tsx：
# VIDEO_FPS/W/H 与 SCENE_FRAMES）。videoUrl 返回 null → 前端走 Remotion Player +
# TTS（契约 8.3）；服务端渲染 mp4 为可选加分项，生产路径：Remotion server-side
# rendering 产出 mp4 回填 videoUrl，写 README。
_VIDEO_FPS = 30
_VIDEO_WIDTH = 1280
_VIDEO_HEIGHT = 720
# 每个分镜场景的时长（帧，30fps→6s/场景），与前端 LectureVideo.SCENE_FRAMES 对齐。
# 场景 i 起始帧 = i * _SCENE_FRAMES；总时长 = 场景数 × _SCENE_FRAMES（随场景数自适应）。
_SCENE_FRAMES = 180


def generate_video(
    db: Session, user_id: str, kp_id: str, difficulty: str
) -> dict[str, Any]:
    """生成视频讲解（接口文档 8.3）。videoUrl:null + 分镜脚本 scenes + narration 映射。

    经 LLMClient.generate_video_script 按当前知识点生成结构化分镜脚本（标题 +
    3-5 个场景，每场景含 title/points/narration）：
    - mock：确定性主题占位脚本（nn 与前端原场景逐字对齐，其余知识点参数化生成）；
    - deepseek：真实生成（解析失败回落主题占位脚本，视频始终可渲染）。

    服务层负责把场景按 _SCENE_FRAMES 均匀铺帧（scenes[].frame、narration[].frame），
    并把整片 durationInFrames 设为场景数 × _SCENE_FRAMES。结果写 ResourceCache
    （kind=video / video@<provider>，按 kp+难度+provider 隔离），命中直接返回，
    避免真实模式重复触发 LLM 调用。
    """
    kp = _require_kp(db, kp_id)
    if difficulty not in LECTURE_DIFFICULTIES:
        raise InvalidDifficulty(difficulty)
    mastery_service.ensure_learning(db, user_id, kp_id)

    llm = get_llm()
    # mock 缓存 kind=video；真实模式按 provider 隔离，互不污染
    kind = "video" if llm.is_mock else f"video@{llm.provider}"
    cached = (
        db.query(ResourceCache)
        .filter(
            ResourceCache.kp_id == kp_id,
            ResourceCache.difficulty == difficulty,
            ResourceCache.kind == kind,
        )
        .one_or_none()
    )
    if cached is not None:
        generation_provenance.mark_cache()
        return dict(cached.payload)

    script = llm.generate_video_script(kp.id, kp.name, difficulty, kp.description or "")
    scenes: list[dict[str, Any]] = []
    narration: list[dict[str, Any]] = []
    for i, s in enumerate(script["scenes"]):
        frame = i * _SCENE_FRAMES
        scenes.append(
            {
                "frame": frame,
                "title": s["title"],
                "points": list(s["points"]),
                "narration": s["narration"],
            }
        )
        narration.append({"frame": frame, "text": s["narration"]})

    payload = {
        "videoUrl": None,  # 无服务端渲染产物 → 前端 Remotion Player + TTS
        "title": script["title"],
        "scenes": scenes,
        "narration": narration,  # 帧-旁白映射（向后兼容，= scenes[].{frame,narration}）
        "fps": _VIDEO_FPS,
        "width": _VIDEO_WIDTH,
        "height": _VIDEO_HEIGHT,
        "durationInFrames": len(scenes) * _SCENE_FRAMES,
    }
    db.add(
        ResourceCache(
            kp_id=kp_id,
            difficulty=difficulty,
            kind=kind,
            payload=payload,
            hallucination_rate=0.0,
        )
    )
    db.commit()
    return payload


def _retrieve_for_lecture(
    kp_id: str, kp_name: str, difficulty: str
) -> list[dict[str, Any]]:
    """讲义检索（B3 混合检索 → 重排），适配工作流 retriever 注入点签名。

    返回结构对齐 B5-a 约定：{id, content, metadata, score}（score 为重排分 0-1，
    重排降级时为 RRF 归一化分，回填 sources[].confidence）。
    """
    query = f"{kp_name} {difficulty}"
    candidates = get_retriever().search(query, top_k=_RETRIEVE_POOL)
    ranked, _used = get_reranker().rerank(query, candidates, top_k=_RETRIEVE_TOP_K)
    return [
        {
            "id": c["id"],
            "content": c["content"],
            "metadata": c.get("metadata") or {},
            "score": float(c.get("score", 0.0)),
        }
        for c in ranked
    ]


def _map_sources(db: Session, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检索切片 → 接口文档 8.2 sources[]（按文档去重，取该文档最高分切片）。

    title = document_title · source_location；type = 文档 category（白名单外回落
    「文档」）；confidence = 该文档命中切片的最高重排分（0-1 截断）。
    """
    best_by_doc: dict[str, dict[str, Any]] = {}
    for c in chunks:
        meta = c.get("metadata") or {}
        # 兼容两种切片元数据形状：真实检索（document_id/document_title）与
        # 工作流确定性 mock 检索（docId/title，见 learning_workflow._mock_retriever）。
        doc_id = meta.get("document_id") or meta.get("docId") or c.get("id", "")
        score = float(c.get("score", 0.0))
        if doc_id not in best_by_doc or score > best_by_doc[doc_id]["score"]:
            best_by_doc[doc_id] = {"meta": meta, "score": score}
    sources: list[dict[str, Any]] = []
    for doc_id, best in best_by_doc.items():
        meta = best["meta"]
        title = meta.get("document_title") or meta.get("title") or "知识库文档"
        location = meta.get("source_location")
        if location:
            title = f"{title} · {location}"
        doc = db.get(KnowledgeDocument, meta.get("document_id") or meta.get("docId") or "")
        category = doc.category if doc is not None else None
        sources.append(
            {
                "title": title,
                "type": category if category in _SOURCE_TYPES else "文档",
                "confidence": round(min(max(best["score"], 0.0), 1.0), 2),
            }
        )
    return sources


def generate_lecture(
    db: Session, user_id: str, kp_id: str, difficulty: str
) -> dict[str, Any]:
    """生成自适应讲义（接口文档 8.2）。命中缓存直接返回。

    mock：LLMClient 确定性产出（B2 等价）；真实模式：RAG 检索 → 生成 Agent →
    审核 Agent 工作流，sources/hallucinationRate 为真实计算值。
    """
    kp = _require_kp(db, kp_id)
    if difficulty not in LECTURE_DIFFICULTIES:
        raise InvalidDifficulty(difficulty)

    # 学习该知识点 → 掌握度置 learning（未开始时）
    mastery_service.ensure_learning(db, user_id, kp_id)

    # 据请求用户画像派生当前知识点的讲义深度档（能力强/零基础产出深度不同的讲义）。
    # 空画像 → tier=None 落难度基线（与 B5b/B10 直出契约一致）。
    portrait = (
        portrait_service.get_portrait(db, user_id) if user_id else {"dimensions": []}
    )
    tier = _ability_tier(portrait, kp_id, kp.name)

    llm = get_llm()
    # mock 缓存 kind 与 B2 完全一致；真实模式按 provider 隔离，互不污染。
    # 个性化分档：tier 非空时按档隔离缓存（lecture#advanced 等），保证不同用户深度互不覆盖；
    # tier=None（无画像信号）→ 维持原 kind=lecture（直出契约不回归）。
    base_kind = "lecture" if llm.is_mock else f"lecture@{llm.provider}"
    kind = base_kind if tier is None else f"{base_kind}#{tier}"
    cached = (
        db.query(ResourceCache)
        .filter(
            ResourceCache.kp_id == kp_id,
            ResourceCache.difficulty == difficulty,
            ResourceCache.kind == kind,
        )
        .one_or_none()
    )
    if cached is not None:
        generation_provenance.mark_cache()
        # 旧缓存（B10 前）可能缺 workflowId 键，统一补 null 后返回（向后兼容）。
        data = dict(cached.payload)
        data.setdefault("workflowId", None)
        return data

    # workflowId（B10）：标识产出该份讲义的工作流 trace，供前端「查看生成过程」回放。
    # mock 直接生成（不经工作流）→ null；真实模式由工作流预分配 id 并落 WorkflowTrace。
    workflow_id: str | None = None
    if llm.is_mock:
        generated = llm.generate_lecture(kp.id, kp.name, difficulty, kp.description, tier)
        markdown = generated["markdown"]
        sources = generated["sources"]
        rate = generated["hallucinationRate"]
    else:
        result = run_learning_workflow(
            db,
            user_id=user_id,
            kp_id=kp_id,
            difficulty=difficulty,
            retriever=_retrieve_for_lecture,
        )
        final = result["finalOutput"]
        markdown = final["markdown"]
        sources = _map_sources(db, result["trace"]["ragContextUsed"])
        rate = final["hallucinationRate"]
        workflow_id = result["workflowId"]

    # 图文并茂：注入图解（图解优先）+ 配图（真实图 / mock 占位），不改返回结构
    markdown = lecture_media.enrich_lecture(
        markdown, kp_id=kp.id, kp_name=kp.name, description=kp.description or "", llm=llm
    )

    payload = {
        "kpId": kp.id,
        "difficulty": difficulty,
        "markdown": markdown,
        "sources": sources,
        "hallucinationRate": rate,
        "workflowId": workflow_id,
    }
    db.add(
        ResourceCache(
            kp_id=kp_id,
            difficulty=difficulty,
            kind=kind,
            payload=payload,
            hallucination_rate=rate,
        )
    )
    db.commit()
    return payload


def cache_lecture_from_workflow(
    db: Session, *, kp_id: str, difficulty: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    """工作流产物回写讲义缓存（B10，打通「工作流产物 → 学习资源」闭环）。

    /workflow/execute 跑完一轮后调用：工作流 **complete 且未降级** 时，把产物
    （markdown + sources + hallucinationRate + workflowId）写入该 kp+难度对应的
    讲义缓存键并**覆盖旧缓存**——使资源页讲义 Tab 与大屏工作流产物一致、workflowId
    一致。降级（degraded）产物**不覆盖缓存**，只保留 WorkflowTrace（旧讲义维持原样）。

    kind 与 generate_lecture 同口径（mock=lecture / 真实=lecture@<provider>），
    保证 /resource/lecture 缓存命中能取到本次回写的产物。

    Returns:
        回写后的讲义 payload；降级或难度档非法时返回 None（不写缓存）。
    """
    final = result.get("finalOutput") or {}
    if final.get("degraded"):
        return None  # 降级产物不覆盖缓存，仅留 trace（旧讲义保留）
    if difficulty not in LECTURE_DIFFICULTIES:
        return None  # 防御：仅按合法讲义难度档回写

    llm = get_llm()
    kind = "lecture" if llm.is_mock else f"lecture@{llm.provider}"
    markdown = final.get("markdown") or ""
    # 与 /resource/lecture 同口径做图文增强，保证大屏工作流回写的讲义同样图文并茂
    kp = db.get(KnowledgePoint, kp_id)
    if kp is not None and markdown:
        markdown = lecture_media.enrich_lecture(
            markdown, kp_id=kp.id, kp_name=kp.name, description=kp.description or "", llm=llm
        )
    sources = _map_sources(db, result.get("trace", {}).get("ragContextUsed") or [])
    rate = final.get("hallucinationRate")
    payload = {
        "kpId": kp_id,
        "difficulty": difficulty,
        "markdown": markdown,
        "sources": sources,
        "hallucinationRate": rate,
        "workflowId": result.get("workflowId"),
    }

    row = (
        db.query(ResourceCache)
        .filter(
            ResourceCache.kp_id == kp_id,
            ResourceCache.difficulty == difficulty,
            ResourceCache.kind == kind,
        )
        .one_or_none()
    )
    if row is not None:
        row.payload = payload  # 覆盖旧缓存
        row.hallucination_rate = rate or 0.0
    else:
        db.add(
            ResourceCache(
                kp_id=kp_id,
                difficulty=difficulty,
                kind=kind,
                payload=payload,
                hallucination_rate=rate or 0.0,
            )
        )
    db.commit()
    return payload
