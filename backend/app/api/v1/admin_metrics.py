"""管理端系统指标看板接口（B4-b，接口文档 14.6）。

GET /admin/metrics —— 供前端三 gauge 仪表盘（CountUp）消费。

结构按契约定死：三比率 + 三计数 + updatedAt。
- 三比率（hallucinationRate/adaptationRate/coverageRate）为 B8 量化指标脚本
  （scripts/metrics/make_report.py）的最近一次实测值，明细见 docs/metrics-report.md；
  updatedAt 即该次计算时间戳。重跑脚本后同步更新 _MEASURED_RATES / _MEASURED_AT。
- 三计数为 DB 实时统计：知识库文档数 / 切片总数 / 已生成资源数。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.envelope import success
from app.core.security import require_admin
from app.models.entities import KnowledgeDocument, ResourceCache, User

router = APIRouter(tags=["admin-metrics"])

# B11 实测比率（docs/metrics-report.md v2，30+ 文档规模化口径，6 KP × 3 难度共 18 份
# deepseek 真实生成讲义；口径与 B8 一致未改动：①15.3 逐句接地 ②三档难度分有序对正确率
# ③核心概念清单命中率。目标：幻觉率 <0.05 ✓、适配率 ≥0.85 ✓、覆盖率 ≥0.90 ✓。
# 知识库由 4 份扩至 30 份（23→153 切片）后三指标仍全部达标，难度适配升至 1.0。
# 重跑 scripts/metrics/make_report_v2.py 后同步本处。）
_MEASURED_AT = "2026-06-12T09:32:51.790457+00:00"
_MEASURED_RATES = {
    "hallucinationRate": 0.0428,
    "adaptationRate": 0.88,
    "coverageRate": 0.93,
}


@router.get("/admin/metrics")
async def get_metrics(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """系统指标看板（接口文档 14.6）。"""
    kb_documents = db.query(func.count(KnowledgeDocument.id)).scalar() or 0
    kb_chunks = db.query(func.coalesce(func.sum(KnowledgeDocument.chunks), 0)).scalar() or 0
    generated_resources = db.query(func.count(ResourceCache.id)).scalar() or 0
    return success(
        {
            **_MEASURED_RATES,
            "kbDocuments": int(kb_documents),
            "kbChunks": int(kb_chunks),
            "generatedResources": int(generated_resources),
            "updatedAt": _MEASURED_AT,  # 三比率的计算时间戳（14.6）
        }
    )
