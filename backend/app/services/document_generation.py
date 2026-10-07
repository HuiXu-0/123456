"""基于文档的内容生成（「文档学习」平行链路核心）。

以**上传文档**为知识源（该文档专属向量集合检索），复用现有生成/审核/防幻觉/内容安全
引擎产出：讲义 / 视频分镜 / 图解 / 思维导图 / 练习题 / 闪卡。与内置课程主线（画像/诊断/
路径/掌握度/既有生成接口）**完全解耦**——不读画像、不写掌握度、不碰 KnowledgePoint。

多文档统一生成（新增）：一次生成可传入多篇文档（documentIds），在**各自专属集合**上分别
检索后合并候选、统一重排，形成「一次生成的合并检索范围」——与内置库隔离、文档间互不污染
（各集合物理独立，仅在本次生成的内存候选池合并）。来源按各文档标题分别标注，可看出内容
来自哪几篇文档。单篇时行为与原先完全一致。

复用点（不重造）：
- 检索：rag.retriever.get_document_retriever（文档专属集合）+ rag.reranker 重排；
- 讲义生成：agents.generator_agent.run_generator（RAG 上下文驱动，mock/real 双模）；
- 防幻觉：rag.grounding.sentence_grounding（逐句 vs 文档切片，mock 环境亦生效）；
- 图解/视频/练习题/闪卡：core.llm.LLMClient 对应方法（内容安全已在 llm 层统一钝化）；
- 内容安全：LLM 生成方法均经 content_safety.guarded；非 LLM 产物（思维导图）显式 guard；
- 资源库：generation_log.record_document 带文档来源标识写入「我的资源库」。
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.agents.generator_agent import run_generator
from app.core import content_safety, generation_provenance, lecture_media
from app.core.llm import LECTURE_DIFFICULTIES, get_llm
from app.models.entities import Document
from app.rag.grounding import sentence_grounding
from app.rag.reranker import get_reranker
from app.rag.retriever import get_document_retriever
from app.rag.vector_store import get_collection_store
from app.services import document_store, generation_log

# 检索候选池 / 注入生成的切片数（与内置讲义 _RETRIEVE_POOL/TOP_K 同量级）
_RETRIEVE_POOL = 12
_RETRIEVE_TOP_K = 6
# 视频分镜铺帧参数（与 services.resource 内置视频、前端 Remotion 组件一致）
_VIDEO_FPS = 30
_VIDEO_WIDTH = 1280
_VIDEO_HEIGHT = 720
_SCENE_FRAMES = 180

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


class DocumentNotReady(Exception):
    """文档尚未完成入库（status != indexed）→ code 1001 / 400。"""


class InvalidDifficulty(Exception):
    """难度档非法 → code 1001 / 400。"""


# ---- 文档归属 / 就绪校验（支持单篇与多篇统一生成） ------------------------
def _require_ready(doc: Document) -> None:
    if doc.status != "indexed":
        raise DocumentNotReady(doc.status)


def resolve_docs(db: Session, user_id: str, doc_ids: list[str]) -> list[Document]:
    """把 documentId(s) 解析为本人、且已就绪的文档列表（顺序保留、去重）。

    任一不存在/非本人 → UnknownDocument（路由转 1004）；任一未就绪 → DocumentNotReady（1001）。
    首篇为「主文档」，用于响应 docId、资源库埋点主键与生成缓存标识。
    """
    seen: set[str] = set()
    docs: list[Document] = []
    for did in doc_ids:
        if not did or did in seen:
            continue
        seen.add(did)
        doc = document_store.require_document(db, user_id, did)
        _require_ready(doc)
        docs.append(doc)
    if not docs:
        raise document_store.UnknownDocument("")
    return docs


def _merged_title(docs: list[Document]) -> str:
    """多文档生成的显示标题：单篇取原标题，多篇标「首篇 等 N 篇文档」。"""
    if len(docs) == 1:
        return docs[0].title
    return f"{docs[0].title} 等 {len(docs)} 篇文档"


# ---- 文档检索（专属集合，隔离内置库；多篇合并候选后统一重排） --------------
def retrieve(docs: list[Document], query: str, top_k: int = _RETRIEVE_TOP_K) -> list[dict[str, Any]]:
    """在每篇文档的专属向量集合上分别检索，合并候选池后统一重排，取前 top_k。

    - 单篇：等价于原先「该集合混合检索 + 重排」；
    - 多篇：各集合分别取候选（隔离），仅在内存候选池合并、统一重排——形成一次生成的
      「合并检索范围」，不建持久化合并集合、不污染任何集合。
    每条候选标注来源文档标题（`__docTitle`），供来源溯源区分多篇出处。
    """
    pooled: list[dict[str, Any]] = []
    for doc in docs:
        cands = get_document_retriever(doc.collection).search(query, top_k=_RETRIEVE_POOL)
        if not cands:
            # 检索为空（如降级嵌入 + 极短文档）→ 直接取集合内切片，保证生成始终有文档上下文
            cands = get_collection_store(doc.collection).get_all()[:_RETRIEVE_POOL]
        for c in cands:
            c["__docTitle"] = doc.title
            pooled.append(c)
    if not pooled:
        return []
    ranked, _used = get_reranker().rerank(query, pooled, top_k=top_k)
    return [
        {
            "id": c.get("id", ""),
            "content": c.get("content", ""),
            "metadata": c.get("metadata") or {},
            "score": float(c.get("score", 0.0)),
            "docTitle": c.get("__docTitle") or docs[0].title,
        }
        for c in ranked
        if c.get("content")
    ]


def _contexts(chunks: list[dict[str, Any]]) -> list[str]:
    return [c["content"] for c in chunks if c.get("content")]


def _summary(chunks: list[dict[str, Any]], *, limit: int = 1200) -> str:
    """文档要点摘要（拼接命中切片，供 description 驱动图解/视频生成，内容来自文档）。"""
    return "\n".join(_contexts(chunks))[:limit]


def sources_of(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检索切片 → 来源标注（按各文档标题 + 切片位置，confidence=重排分）。

    多篇统一生成时，来源标题分别带各自文档名，可直接看出内容来自哪几篇文档。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in chunks:
        meta = c.get("metadata") or {}
        loc = meta.get("source_location") or "正文"
        title = f"{c.get('docTitle') or '文档'} · {loc}"
        if title in seen:
            continue
        seen.add(title)
        out.append(
            {
                "title": title,
                "type": "文档",
                "confidence": round(min(max(float(c.get("score", 0.0)), 0.0), 1.0), 2),
            }
        )
    return out


# ---- 讲义（复用生成 Agent + 逐句接地防幻觉） --------------------------------
def generate_lecture(
    db: Session,
    user_id: str,
    doc_ids: list[str],
    difficulty: str = "初级",
    regenerate: bool = False,
) -> dict[str, Any]:
    """基于文档生成自适应讲义。复用 generator_agent（RAG 驱动）+ sentence_grounding（防幻觉）。

    产物落库：非 ``regenerate`` 时**先读已落库产物**、命中直接返回（「查看」秒开、0 生成
    调用、内容与生成时一致）；未命中或显式 ``regenerate=True`` 才实时生成并写入/覆盖产物。
    """
    if difficulty not in LECTURE_DIFFICULTIES:
        raise InvalidDifficulty(difficulty)
    if not regenerate:
        cached = generation_log.get_document_artifact(
            db, user_id, doc_ids[0] if doc_ids else "", "lecture", difficulty
        )
        if cached is not None:
            generation_provenance.mark_cache()
            return cached
    docs = resolve_docs(db, user_id, doc_ids)
    primary = docs[0]
    title = _merged_title(docs)

    chunks = retrieve(docs, f"{title} 核心概念 重点", top_k=_RETRIEVE_TOP_K)
    res = run_generator(
        db,
        kp_name=title,
        difficulty=difficulty,
        rag_context=chunks,
        description=_summary(chunks),
    )
    markdown = res["output"]["markdown"]
    # 图文增强（复用讲义配图/图解注入；kp_id 传文档专属标识，不与内置知识点冲突）
    markdown = lecture_media.enrich_lecture(
        markdown, kp_id=f"doc:{primary.id}", kp_name=title, description=_summary(chunks), llm=get_llm()
    )
    # 防幻觉：逐句 vs 文档切片接地校验（对文档而非内置课程，mock/降级环境同样生效）
    grounding = sentence_grounding(markdown, _contexts(chunks))
    payload = {
        "docId": primary.id,
        "docIds": [d.id for d in docs],
        "difficulty": difficulty,
        "markdown": markdown,
        "sources": sources_of(chunks),
        "hallucinationRate": grounding["hallucinationRate"],
    }
    generation_log.record_document(
        db, user_id, primary.id, title, "lecture", difficulty=difficulty, artifact=payload
    )
    return payload


# ---- 文档概览（NotebookLM 式速读，复用 LLMClient.generate_overview，文档内容驱动） ----
def generate_overview(db: Session, user_id: str, doc_id: str) -> dict[str, Any]:
    """基于文档生成「文档概览」：这篇文档是什么、讲了什么、结构概况与关键点。

    走文档专属向量集合检索（隔离内置库），内容严格来自文档（防幻觉）；LLMClient 双模 + mock 兜底、
    经内容安全钝化。概览为「选中文档即自动展示」的速读入口，不计入资源库产物（不落 generation_log）。
    概览按单篇文档生成（左栏「关于来源」信息区），不做多篇合并。
    """
    # 先查缓存：同一文档已生成过概览则直接返回，不重复调 LLM
    cached = generation_log.get_document_artifact(db, user_id, doc_id, "overview")
    if cached is not None:
        generation_provenance.mark_cache()
        return cached

    doc = document_store.require_document(db, user_id, doc_id)
    _require_ready(doc)
    chunks = retrieve([doc], f"{doc.title} 概览 简介 主要内容 结构")
    overview = get_llm().generate_overview(doc.title, _contexts(chunks))
    result = {"docId": doc_id, "overview": overview, "sources": sources_of(chunks)}

    # 记录产物到缓存（与讲义/图解等接口一致），后续再查直接命中
    generation_log.record_document(db, user_id, doc_id, doc.title, "overview", artifact=result)
    return result


# ---- 图解（复用 LLMClient.generate_diagram，文档内容驱动） ------------------
def generate_diagram(
    db: Session, user_id: str, doc_ids: list[str], regenerate: bool = False
) -> dict[str, Any]:
    if not regenerate:
        cached = generation_log.get_document_artifact(
            db, user_id, doc_ids[0] if doc_ids else "", "diagram"
        )
        if cached is not None:
            generation_provenance.mark_cache()
            return cached
    docs = resolve_docs(db, user_id, doc_ids)
    primary = docs[0]
    title = _merged_title(docs)
    chunks = retrieve(docs, f"{title} 结构 流程 关系")
    payload = get_llm().generate_diagram(f"doc:{primary.id}", title, _summary(chunks))
    result = {
        "docId": primary.id,
        "docIds": [d.id for d in docs],
        "mermaid": payload["mermaid"],
        "sources": sources_of(chunks),
    }
    generation_log.record_document(db, user_id, primary.id, title, "diagram", artifact=result)
    return result


# ---- 思维导图（从文档标题/结构确定性抽取，内容来自文档） --------------------
def generate_mindmap(
    db: Session, user_id: str, doc_ids: list[str], regenerate: bool = False
) -> dict[str, Any]:
    if not regenerate:
        cached = generation_log.get_document_artifact(
            db, user_id, doc_ids[0] if doc_ids else "", "mindmap"
        )
        if cached is not None:
            generation_provenance.mark_cache()
            return cached
    docs = resolve_docs(db, user_id, doc_ids)
    primary = docs[0]
    title = _merged_title(docs)
    markdown = _build_mindmap(docs, title)
    markdown = content_safety.guard(markdown, where="document_mindmap")
    generation_provenance.mark_deterministic()
    result = {"docId": primary.id, "docIds": [d.id for d in docs], "markdown": markdown}
    generation_log.record_document(db, user_id, primary.id, title, "mindmap", artifact=result)
    return result


def _build_mindmap(docs: list[Document], title: str) -> str:
    """思维导图大纲（Markmap）。

    - 单篇：优先按文档 Markdown 标题层级构建；无标题则用要点句作二级节点。
    - 多篇：以各文档标题为二级节点，其下挂各自要点句（体现来自多篇）。
    """
    if len(docs) > 1:
        lines = [f"# {title}"]
        for doc in docs:
            lines.append(f"## {doc.title[:40]}")
            chunks = retrieve([doc], f"{doc.title} 要点", top_k=4)
            from app.core.llm import _doc_key_sentences

            for sent in _doc_key_sentences(_contexts(chunks), max_n=4):
                lines.append(f"### {sent[:36]}")
        return "\n".join(lines)

    doc = docs[0]
    lines = [f"# {doc.title}"]
    headings = [
        (len(m.group(1)), m.group(2).strip())
        for raw in (doc.content or "").splitlines()
        if (m := _HEADING_RE.match(raw.strip()))
    ]
    if len(headings) >= 2:
        base = min(lv for lv, _ in headings)
        for lv, htitle in headings[:40]:
            # 归一到二级起（# 已占一级）：相对深度 + 1
            depth = min(lv - base + 2, 6)
            lines.append(f"{'#' * depth} {htitle[:40]}")
        return "\n".join(lines)
    # 无标题结构 → 取文档要点句作二级节点（内容来自文档）
    chunks = retrieve([doc], f"{doc.title} 要点", top_k=6)
    from app.core.llm import _doc_key_sentences  # 复用确定性要点抽取

    for sent in _doc_key_sentences(_contexts(chunks), max_n=8):
        lines.append(f"## {sent[:36]}")
    if len(lines) == 1:
        lines.append("## 核心概念")
        lines.append("## 实践应用")
    return "\n".join(lines)


# ---- 视频分镜（复用 LLMClient.generate_video_script + 铺帧） ----------------
def generate_video(
    db: Session,
    user_id: str,
    doc_ids: list[str],
    difficulty: str = "初级",
    regenerate: bool = False,
) -> dict[str, Any]:
    if difficulty not in LECTURE_DIFFICULTIES:
        raise InvalidDifficulty(difficulty)
    if not regenerate:
        cached = generation_log.get_document_artifact(
            db, user_id, doc_ids[0] if doc_ids else "", "video", difficulty
        )
        if cached is not None:
            generation_provenance.mark_cache()
            return cached
    docs = resolve_docs(db, user_id, doc_ids)
    primary = docs[0]
    title = _merged_title(docs)
    chunks = retrieve(docs, f"{title} 讲解 要点")
    script = get_llm().generate_video_script(
        f"doc:{primary.id}", title, difficulty, _summary(chunks)
    )
    scenes: list[dict[str, Any]] = []
    narration: list[dict[str, Any]] = []
    for i, s in enumerate(script["scenes"]):
        frame = i * _SCENE_FRAMES
        scenes.append(
            {"frame": frame, "title": s["title"], "points": list(s["points"]), "narration": s["narration"]}
        )
        narration.append({"frame": frame, "text": s["narration"]})
    result = {
        "docId": primary.id,
        "docIds": [d.id for d in docs],
        "difficulty": difficulty,
        "videoUrl": None,  # 无服务端渲染 → 前端 Remotion Player + TTS（与内置 8.3 同口径）
        "title": script["title"],
        "scenes": scenes,
        "narration": narration,
        "fps": _VIDEO_FPS,
        "width": _VIDEO_WIDTH,
        "height": _VIDEO_HEIGHT,
        "durationInFrames": len(scenes) * _SCENE_FRAMES,
        "sources": sources_of(chunks),
    }
    generation_log.record_document(
        db, user_id, primary.id, title, "video", difficulty=difficulty, artifact=result
    )
    return result


# ---- 练习题（复用 LLMClient.generate_doc_quiz + audit_practice 审核） -------
def generate_quiz(
    db: Session, user_id: str, doc_ids: list[str], count: int = 5, regenerate: bool = False
) -> dict[str, Any]:
    if not regenerate:
        cached = generation_log.get_document_artifact(
            db, user_id, doc_ids[0] if doc_ids else "", "quiz"
        )
        if cached is not None:
            generation_provenance.mark_cache()
            return cached
    docs = resolve_docs(db, user_id, doc_ids)
    primary = docs[0]
    title = _merged_title(docs)
    chunks = retrieve(docs, f"{title} 重点 考点", top_k=_RETRIEVE_TOP_K)
    result = get_llm().generate_doc_quiz(title, _contexts(chunks), count=count)
    payload = {
        "docId": primary.id,
        "docIds": [d.id for d in docs],
        "questions": result["questions"],
        "sources": sources_of(chunks),
    }
    generation_log.record_document(db, user_id, primary.id, title, "quiz", artifact=payload)
    return payload


# ---- 闪卡（新做，走 LLMClient，mock 确定性兜底） ---------------------------
def generate_flashcards(
    db: Session, user_id: str, doc_ids: list[str], count: int = 8, regenerate: bool = False
) -> dict[str, Any]:
    if not regenerate:
        cached = generation_log.get_document_artifact(
            db, user_id, doc_ids[0] if doc_ids else "", "flashcard"
        )
        if cached is not None:
            generation_provenance.mark_cache()
            return cached
    docs = resolve_docs(db, user_id, doc_ids)
    primary = docs[0]
    title = _merged_title(docs)
    chunks = retrieve(docs, f"{title} 要点 概念", top_k=_RETRIEVE_TOP_K)
    result = get_llm().generate_flashcards(title, _contexts(chunks), count=count)
    payload = {
        "docId": primary.id,
        "docIds": [d.id for d in docs],
        "cards": result["cards"],
        "sources": sources_of(chunks),
    }
    generation_log.record_document(db, user_id, primary.id, title, "flashcard", artifact=payload)
    return payload
