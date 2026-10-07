"""画像诊断服务（B2-a mock 数据源 / B5-b 真实抽取与叙述经 LLMClient 分支）。

覆盖接口文档第 4 章：
- 4.1 parse：multipart 材料 → pypdf/文本抽取 → LLMClient.extract_profile 输出
  education/major/goal + 6 维画像（mock 确定性 / deepseek 真实抽取+契约清洗）；
  无任何材料时 source=manual、不编造经历（防幻觉约束，真实模式也不调用 LLM）。
- 4.2 narrative：经 LLMClient 生成两段带 sourceId 叙述；无材料时返回 None（叙述 null）。
- 4.3 diagnosis-complete：写入 Journey（hasDiagnosed/targetJobName/matchPct）。
- 4.4 ability-portrait：返回 6 维雷达；以最近一次 parse 结果为准，无则用基线默认。

所有生成调用经 `app.core.llm.LLMClient`（CLAUDE.md 工程纪律）。最近画像按 userId
存于进程内存（轻量栈；生产替换会话存储），使 parse → ability-portrait 链路一致。
"""
from __future__ import annotations

import io
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import ABILITY_DIMENSIONS, get_llm
from app.models.entities import Journey, User
from app.schemas.profile import DiagnosisCompleteRequest, NarrativeRequest
from app.services import knowledge_catalog
from app.services import mastery as mastery_service

# 图片扩展名（mock 阶段不做真实 OCR，仅登记材料 + 标注 source=ocr）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# 最近一次 parse 的能力值，按 userId 缓存（进程内存）
_portrait_by_user: dict[str, list[int]] = {}


def _kind_for(filename: str) -> str:
    lowered = filename.lower()
    if any(lowered.endswith(ext) for ext in _IMAGE_EXTS):
        return "image"
    return "doc"


def _extract_text(filename: str, content: bytes) -> str:
    """从上传文件抽取文本：pdf 走 pypdf，txt/md 直接解码，其余忽略。"""
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:  # noqa: BLE001 抽取失败不致命，按无文本处理
            return ""
    if lowered.endswith((".txt", ".md")):
        try:
            return content.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            return ""
    return ""


def parse_profile(
    user: User,
    uploads: list[tuple[str, bytes]],
    description: str | None,
) -> dict[str, Any]:
    """解析材料为结构化画像（接口文档 4.1 ParsedProfile）。

    uploads: [(filename, content_bytes)]；description: 自由文本背景，可空。
    """
    materials: list[dict[str, str]] = []
    doc_texts: list[str] = []
    has_doc = False
    has_image = False

    for idx, (filename, content) in enumerate(uploads, start=1):
        kind = _kind_for(filename)
        materials.append({"id": f"m{idx}", "label": filename, "kind": kind})
        if kind == "image":
            has_image = True
        else:
            has_doc = True
            doc_texts.append(_extract_text(filename, content))

    desc = (description or "").strip()
    if desc:
        materials.append(
            {"id": f"m{len(materials) + 1}", "label": "背景描述", "kind": "text"}
        )

    # 来源优先级：有文档→resume；仅图片→ocr；仅文本→text；全空→manual（不编造）
    if has_doc:
        source = "resume"
    elif has_image:
        source = "ocr"
    elif desc:
        source = "text"
    else:
        source = "manual"

    combined_text = "\n".join(doc_texts + ([desc] if desc else []))
    # mock：确定性产出（B2 等价）；真实模式：LLM 抽取 + 契约清洗。
    # 无任何材料文本时（manual）一律确定性基线，不调用 LLM 编造经历（防幻觉约束）。
    extracted = get_llm().extract_profile(combined_text, source=source)
    skills = extracted["skills"]

    # 缓存能力值供 ability-portrait 读取
    _portrait_by_user[user.id] = [int(s["level"]) for s in skills]

    return {
        "education": {"value": extracted["education"], "source": source},
        "major": {"value": extracted["major"], "source": source},
        "goal": {"value": extracted["goal"], "source": source},
        "skills": skills,
        "materials": materials,
    }


def generate_narrative(req: NarrativeRequest) -> dict[str, Any] | None:
    """生成两段式画像叙述（接口文档 4.2）。

    无材料时返回 None——与前端 generateNarrative 无材料返回 null 一致（防幻觉）。
    """
    materials = [m.model_dump() for m in req.materials]
    if not materials:
        return None

    draft = req.draft.model_dump()
    target_job = req.targetJob.model_dump() if req.targetJob else None
    paragraphs = get_llm().generate_narrative(draft, materials, target_job)

    return {
        "paragraphs": paragraphs,
        "sources": materials,
        "materialCount": len(materials),
    }


def complete_diagnosis(
    db: Session, user: User, req: DiagnosisCompleteRequest
) -> dict[str, Any]:
    """写入旅程诊断状态（接口文档 4.3）。"""
    journey = db.get(Journey, user.id)
    if journey is None:
        journey = Journey(user_id=user.id)
        db.add(journey)
    journey.has_diagnosed = True
    journey.target_job_name = req.targetJobName
    journey.match_pct = req.matchPct
    # 清除路径缓存：诊断完成后下次 GET /learning-path 重新规划个性化路径
    journey.path_plan = None
    journey.path_fingerprint = None
    journey.path_narrative = None
    db.commit()
    return {"hasDiagnosed": True}


def _axis_value(entry: dict[str, Any] | None) -> int:
    """知识点能力分 → 雷达轴值（C2：靠测，不臆造）。

    优先用 Mastery 实测分（诊断微测/真实 quiz 写入）；无分时按状态保守回落：
    passed→80（已通过测验，可信高分）、learning/pending-check→50、未测→0（不臆造）。
    """
    if entry is None:
        return 0  # 未测
    score = entry.get("score")
    if isinstance(score, int):
        return max(0, min(100, score))
    status = entry.get("status")
    if status == mastery_service.STATUS_PASSED:
        return 80
    if status in (mastery_service.STATUS_LEARNING, mastery_service.STATUS_PENDING_CHECK):
        return 50
    return 0


def ability_portrait(db: Session, user: User) -> dict[str, Any]:
    """能力雷达 6 维（接口文档 4.4，C2 真实化）。

    由**诊断微测 / 真实 quiz 写入 Mastery 的能力分**驱动（按知识点 lesson_seq 升序
    定位 6 轴），不再读简历自陈或写死基线——能力靠测、口径与掌握度统一。未测知识点
    轴值回落 0（不臆造高分）。
    """
    score_map = mastery_service.get_score_map(db, user.id)
    # 能力雷达固定 6 轴（接口文档 4.4）：仍取 6 已验证基准点（体系扩到 78 点不改）
    kps = sorted(knowledge_catalog.core_kps(db), key=lambda k: k.lesson_seq)
    values = [_axis_value(score_map.get(kp.id)) for kp in kps]
    # 防御：知识点数与固定 6 维不齐时对齐到 6 维（补 0 / 截断），保证契约稳定
    if len(values) < len(ABILITY_DIMENSIONS):
        values += [0] * (len(ABILITY_DIMENSIONS) - len(values))
    return {"dimensions": list(ABILITY_DIMENSIONS), "values": values[: len(ABILITY_DIMENSIONS)]}
