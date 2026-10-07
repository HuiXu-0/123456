"""智能辅导·按需资源生成服务（接口文档 8.8，C-fix 批3-bonus）。

学生在导学对话里提问 / 点「我没懂」→ 识别问题点 → 给出针对性「资源生成清单」
（图解 / 例题 / 短视频 / 补充讲义片段）→ 学生勾选 → 按需生成对应资源。

**复用现有生成能力**，不重建：
- diagram → `resource_service.diagram`（8.5 Mermaid 图解）
- video   → `resource_service.generate_video`（8.3 分镜脚本）
- example → `LLMClient.generate_remedial_content("example", ...)`（例题 + 解析）
- lecture → `LLMClient.generate_remedial_content("lecture", ...)`（针对性讲义片段）

问题点识别与清单/内容生成均经 `LLMClient`（mock 确定性 / deepseek 真实），mock 兜底
保证无 Key 可跑。资源生成清单接口套统一信封。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import REMEDIAL_TYPES, get_llm
from app.services import resource as resource_service

# 按需生成时讲义/视频复用的难度档（辅导场景固定「初级」，聚焦补缺、不做难度分级）
_REMEDIAL_DIFFICULTY = "初级"


def suggest(db: Session, kp_id: str, question: str) -> dict[str, Any]:
    """识别问题点 + 针对性资源生成清单（接口文档 8.8）。知识点不存在 → 抛 UnknownKnowledgePoint。"""
    kp = resource_service._require_kp(db, kp_id)
    out = get_llm().suggest_remedial_resources(kp.name, question or "")
    return {
        "kpId": kp.id,
        "kpName": kp.name,
        "problemPoint": out["problemPoint"],
        "suggestions": out["suggestions"],
    }


def _generate_one(
    db: Session, user_id: str, kp, problem_point: str, rtype: str
) -> dict[str, Any] | None:
    """生成单项资源（复用既有生成能力）。未知类型 → None（跳过）。"""
    if rtype == "diagram":
        data = resource_service.diagram(db, kp.id)  # {mermaid}
        return {
            "type": "diagram",
            "title": f"知识图解 · {problem_point}",
            "mermaid": data.get("mermaid", ""),
        }
    if rtype == "video":
        data = resource_service.generate_video(db, user_id, kp.id, _REMEDIAL_DIFFICULTY)
        return {
            "type": "video",
            "title": data.get("title") or f"短视频讲解 · {problem_point}",
            "scenes": data.get("scenes", []),
            "durationInFrames": data.get("durationInFrames", 0),
            "fps": data.get("fps"),
        }
    if rtype == "example":
        data = get_llm().generate_remedial_content("example", kp.name, problem_point)
        return {"type": "example", **data}
    if rtype == "lecture":
        data = get_llm().generate_remedial_content("lecture", kp.name, problem_point)
        return {"type": "lecture", **data}
    return None


def generate(
    db: Session,
    user_id: str,
    kp_id: str,
    problem_point: str | None,
    types: list[str],
) -> dict[str, Any]:
    """按需生成勾选的针对性资源（接口文档 8.8）。

    types 取白名单内去重子集；problemPoint 缺省回落知识点核心概念。复用现有生成能力，
    逐项产出 results[]，前端可直接渲染/查看。
    """
    kp = resource_service._require_kp(db, kp_id)
    point = (problem_point or "").strip() or f"{kp.name}的核心概念"
    # 去重并保持 REMEDIAL_TYPES 稳定顺序
    chosen = [t for t in REMEDIAL_TYPES if t in set(types)]

    results: list[dict[str, Any]] = []
    for rtype in chosen:
        item = _generate_one(db, user_id, kp, point, rtype)
        if item is not None:
            results.append(item)
    return {"kpId": kp.id, "problemPoint": point, "results": results}
