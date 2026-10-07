"""Run TASK-004-D offline fault injection without production mutation."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.rag.canary_fault_injection import CanaryFaultInjectionEvaluator


def main() -> None:
    parser = argparse.ArgumentParser(description="TASK-004-D offline Trusted RAG fault injection")
    parser.add_argument(
        "--output",
        default=str(_BACKEND_ROOT / "evaluation" / "trusted_rag_fault_results.json"),
    )
    parser.add_argument("--hard-timeout-budget-ms", type=float, default=10.0)
    parser.add_argument("--hang-probe-ms", type=float, default=80.0)
    args = parser.parse_args()

    report = CanaryFaultInjectionEvaluator(
        hard_timeout_budget_ms=args.hard_timeout_budget_ms,
        hang_probe_ms=args.hang_probe_ms,
    ).evaluate()
    payload = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "evaluationType": "offline_fault_injection",
        "productionPerformance": False,
        "environment": {
            "runtime": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
            },
            "hardTimeoutBudgetMs": args.hard_timeout_budget_ms,
            "hangProbeMs": args.hang_probe_ms,
        },
        **report.model_dump(mode="json"),
        "limitations": [
            "No production API, Agent, Workflow, Service or route is mutated.",
            "The hard-hang probe is bounded and validates the E2 offline Shadow deadline/isolation layer.",
            "Python cannot forcibly terminate a blocking thread; daemon isolation, cooperative cancellation and bounded slots contain it.",
            "LLM failure is tested at the candidate-generation boundary because Trusted RAG Service has no LLM call.",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
