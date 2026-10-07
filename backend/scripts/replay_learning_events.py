"""按时间顺序重放已落库 LearningEvent，并输出 KnowledgeState Shadow 报告。

用法：python scripts/replay_learning_events.py --user-id u_10001 [--knowledge-id ml]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.database import SessionLocal
from app.services.knowledge_state import KnowledgeStateService
from app.services.knowledge_state_shadow import evaluate_shadow


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay LearningEvent history")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--knowledge-id")
    args = parser.parse_args()

    with SessionLocal() as db:
        events = KnowledgeStateService(db).get_history(args.user_id, args.knowledge_id)
    report = evaluate_shadow(events)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
