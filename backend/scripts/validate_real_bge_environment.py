"""独立验证真实 BGE 环境；不导入 app，不允许任何 fallback。"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
EXPECTED_DIMENSION = 512
EXPECTED_PROFILE = "sentence-transformers:baai_bge_small_zh_v1_5:d512"


def main() -> int:
    parser = argparse.ArgumentParser(description="验证真实 BGE Embedding 环境")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()
    report: dict[str, object] = {
        "status": "blocked",
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "model": MODEL_NAME,
        "expectedDimension": EXPECTED_DIMENSION,
        "expectedProfileId": EXPECTED_PROFILE,
        "fallbackAllowed": False,
    }
    try:
        import torch

        report["torch"] = torch.__version__
        report["cudaBuild"] = torch.version.cuda
        report["cudaAvailable"] = torch.cuda.is_available()
    except Exception as exc:  # noqa: BLE001 - 诊断入口必须保留原始失败类型
        report["failedStage"] = "import_torch"
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(
            MODEL_NAME,
            cache_folder=str(args.cache_dir) if args.cache_dir else None,
            local_files_only=args.local_files_only,
            device="cpu",
        )
        model_dimension = int(model.get_sentence_embedding_dimension())
        vector = model.encode(
            ["神经网络通过反向传播更新参数。"],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        vector_dimension = len(vector)
        report.update(
            {
                "modelDimension": model_dimension,
                "vectorDimension": vector_dimension,
                "profileMatches": model_dimension == EXPECTED_DIMENSION,
                "vectorMatches": vector_dimension == EXPECTED_DIMENSION,
            }
        )
        if model_dimension != EXPECTED_DIMENSION or vector_dimension != EXPECTED_DIMENSION:
            report["failedStage"] = "dimension_validation"
            report["error"] = "真实模型或输出向量维度与EmbeddingProfile不一致"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 3
    except Exception as exc:  # noqa: BLE001
        report["failedStage"] = "load_or_encode_model"
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    report["status"] = "passed"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
