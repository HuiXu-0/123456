"""输出 TASK-005-E Shadow 7/30 天观测与校准门槛 JSON。"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.init_db import init_db  # noqa: E402
from app.models.entities import LearningEventAnomalyRecord, LearningEventRecord  # noqa: E402
from app.schemas.knowledge_state import LearningEvent  # noqa: E402
from app.services.knowledge_state_calibration import build_calibration_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="UTC ISO-8601 截止时间；默认当前时间")
    args = parser.parse_args()
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else datetime.now(timezone.utc)
    init_db()
    with SessionLocal() as db:
        rows = db.query(LearningEventRecord).order_by(LearningEventRecord.timestamp, LearningEventRecord.event_id).all()
        events = [LearningEvent(
            event_id=row.event_id, user_id=row.user_id, knowledge_id=row.knowledge_id,
            event_type=row.event_type, source_type=row.source_type, source_id=row.source_id,
            algorithm_version=row.algorithm_version, score=row.score,
            timestamp=row.timestamp.replace(tzinfo=timezone.utc),
        ) for row in rows]
        anomaly_start = as_of.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        anomaly_rows = db.query(LearningEventAnomalyRecord).filter(
            LearningEventAnomalyRecord.created_at > anomaly_start,
            LearningEventAnomalyRecord.created_at <= as_of.astimezone(timezone.utc).replace(tzinfo=None),
        ).all()
        anomaly_counts = Counter(row.anomaly_type for row in anomaly_rows)
    print(json.dumps(build_calibration_report(events, as_of=as_of, anomaly_counts=anomaly_counts), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
