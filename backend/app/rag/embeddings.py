"""文本向量化（B3，bge-small-zh-v1.5 本地加载 + 降级）。

设计要点（对齐任务约束）：
- **延迟加载不阻塞 uvicorn**：进程启动时不加载模型；首次 `embed_*` 调用时才尝试
  `SentenceTransformer(model_name, cache_folder=model_cache_dir)`。
- **加载失败自动降级**：sentence-transformers 未安装 / 模型下载失败 / 加载异常时，
  切换为确定性哈希嵌入（char-ngram 散列到固定维度并 L2 归一化），打 WARNING 日志，
  保证全链路在无网络、无模型文件时仍可跑通（CLAUDE.md「无密钥可跑通」纪律）。
- 单例：`get_embedder()` 返回进程内单例。
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.core.config import settings

logger = logging.getLogger("app.rag.embeddings")

# Windows 已知问题（B8 修复）：torch DLL 若在**非主线程**首次加载会 access violation
# 直接崩溃进程（kb 入库走 ThreadPoolExecutor 工作线程触发）。在模块导入期（主线程、
# 应用启动时）先行导入 torch 栈规避；未安装时静默跳过，保持下方哈希降级路径不变。
try:  # noqa: SIM105
    import sentence_transformers  # noqa: F401
except Exception:  # noqa: BLE001
    pass

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


class EmbeddingConfigurationError(RuntimeError):
    """Embedding配置与模型实际能力不一致。"""


class EmbeddingDimensionError(ValueError):
    """向量维度不符合当前Embedding Profile。"""


class EmbeddingUnavailableError(RuntimeError):
    """Embedding不可用且策略不允许降级。"""


class EmbeddingRuntimeMode(str, Enum):
    REAL = "real_embedding"
    HASH = "hash_fallback"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class EmbeddingProfile:
    """唯一的Embedding空间契约。"""

    provider: str
    model_name: str
    dimension: int

    @property
    def profile_id(self) -> str:
        safe_model = re.sub(r"[^a-zA-Z0-9]+", "_", self.model_name).strip("_").lower()
        return f"{self.provider}:{safe_model}:d{self.dimension}"


def get_embedding_profile() -> EmbeddingProfile:
    profile = EmbeddingProfile(
        provider=settings.embedding_provider.strip().lower(),
        model_name=settings.embedding_model_name.strip(),
        dimension=settings.embedding_dimension,
    )
    if not profile.provider or not profile.model_name:
        raise EmbeddingConfigurationError("Embedding provider和model_name不能为空")
    if profile.provider not in {"sentence-transformers", "hash"}:
        raise EmbeddingConfigurationError(
            f"不支持的Embedding provider：{profile.provider}"
        )
    if profile.dimension <= 0:
        raise EmbeddingConfigurationError("Embedding dimension必须大于0")
    return profile


def validate_vector_dimension(
    vector: list[float], *, expected: int, context: str
) -> None:
    actual = len(vector)
    if actual != expected:
        raise EmbeddingDimensionError(
            f"{context}向量维度不一致：expected={expected}, actual={actual}"
        )


def model_is_cached(model_name: str) -> bool:
    """模型是否已下载到本地缓存（HF 缓存目录命名 models--ORG--NAME）。

    已缓存时以 local_files_only 离线加载，避免运行时网络调用（uvicorn/async 环境下
    huggingface_hub 的 httpx 客户端可能已关闭，导致联网加载报错而误降级）。
    """
    safe = "models--" + model_name.replace("/", "--")
    return os.path.isdir(os.path.join(settings.model_cache_dir, safe))


class Embedder:
    """向量化适配层：优先 bge-small-zh，失败降级为哈希嵌入。"""

    def __init__(
        self,
        *,
        profile: EmbeddingProfile | None = None,
        allow_fallback: bool | None = None,
    ) -> None:
        self.profile = profile or get_embedding_profile()
        self.allow_fallback = (
            settings.embedding_allow_fallback
            if allow_fallback is None
            else allow_fallback
        )
        self.model_name = self.profile.model_name
        self.fallback_dim = self.profile.dimension
        self._model = None  # type: ignore[var-annotated]
        self._loaded = False  # 是否已尝试加载（无论成功失败）
        self._configuration_error: EmbeddingConfigurationError | None = None
        self._unavailable_error: EmbeddingUnavailableError | None = None
        self.degraded = False  # True 表示当前走哈希降级
        self.runtime_mode = EmbeddingRuntimeMode.UNAVAILABLE
        self.load_error: str | None = None
        self._lock = threading.Lock()

    # ---- 模型加载（延迟、线程安全、失败降级） ----------------------------
    def _ensure_loaded(self) -> None:
        if self._unavailable_error is not None:
            raise self._unavailable_error
        if self._configuration_error is not None:
            raise self._configuration_error
        if self._loaded:
            return
        with self._lock:
            if self._unavailable_error is not None:
                raise self._unavailable_error
            if self._configuration_error is not None:
                raise self._configuration_error
            if self._loaded:
                return
            if self.profile.provider == "hash":
                self._model = None
                self.degraded = True
                self.runtime_mode = EmbeddingRuntimeMode.HASH
                self._loaded = True
                return
            try:
                from sentence_transformers import SentenceTransformer

                cached = model_is_cached(self.model_name)
                self._model = SentenceTransformer(
                    self.model_name,
                    cache_folder=settings.model_cache_dir,
                    local_files_only=cached,  # 已缓存则离线加载，避免运行时联网
                )
                actual_dimension = int(
                    self._model.get_sentence_embedding_dimension()
                )
                if actual_dimension != self.profile.dimension:
                    raise EmbeddingConfigurationError(
                        "Embedding模型维度与配置不一致："
                        f"model={self.model_name}, expected={self.profile.dimension}, "
                        f"actual={actual_dimension}"
                    )
                self.degraded = False
                self.runtime_mode = EmbeddingRuntimeMode.REAL
                logger.info(
                    "嵌入模型加载成功：%s（%s）",
                    self.model_name,
                    "离线缓存" if cached else "在线下载",
                )
            except EmbeddingConfigurationError as exc:
                self._model = None
                self.degraded = False
                self._configuration_error = exc
                self.runtime_mode = EmbeddingRuntimeMode.UNAVAILABLE
                self.load_error = str(exc)
                raise
            except Exception as exc:  # noqa: BLE001 任何运行失败都降级，不阻断
                self._model = None
                self.load_error = f"{type(exc).__name__}: {exc}"
                if not self.allow_fallback:
                    self.degraded = False
                    self.runtime_mode = EmbeddingRuntimeMode.UNAVAILABLE
                    error = EmbeddingUnavailableError(
                        f"真实Embedding加载失败且禁止fallback：{self.load_error}"
                    )
                    self._unavailable_error = error
                    raise error from exc
                self.degraded = True
                self.runtime_mode = EmbeddingRuntimeMode.HASH
                logger.warning(
                    "嵌入模型 '%s' 加载失败，降级为确定性哈希嵌入（dim=%d）：%s",
                    self.model_name,
                    self.fallback_dim,
                    exc,
                )
            finally:
                self._loaded = True

    # ---- 对外 API ---------------------------------------------------------
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。返回与输入等长的向量列表。"""
        self._ensure_loaded()
        if not texts:
            return []
        if self._model is not None:
            vecs = self._model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
            outputs = [list(map(float, v)) for v in vecs]
        else:
            outputs = [self._hash_embed(t) for t in texts]
        for index, vector in enumerate(outputs):
            validate_vector_dimension(
                vector,
                expected=self.profile.dimension,
                context=f"Embedding输出[{index}]",
            )
        return outputs

    def embed_query(self, text: str) -> list[float]:
        """单条查询向量化。"""
        return self.embed_texts([text])[0]

    def status(self, *, load: bool = True) -> dict[str, Any]:
        """返回可审计运行状态；不把hash fallback标记为真实BGE。"""
        if load:
            self._ensure_loaded()
        return {
            "mode": self.runtime_mode.value,
            "profileId": self.profile.profile_id,
            "provider": self.profile.provider,
            "modelName": self.profile.model_name,
            "dimension": self.profile.dimension,
            "fallbackAllowed": self.allow_fallback,
            "loadError": self.load_error,
        }

    def require_real(self) -> dict[str, Any]:
        """评测强制真实模型；失败必须终止，不允许静默降级。"""
        status = self.status(load=True)
        if self.runtime_mode != EmbeddingRuntimeMode.REAL:
            raise EmbeddingUnavailableError(
                "评测要求real_embedding，但当前模式为"
                f"{self.runtime_mode.value}：{self.load_error or 'provider不是实际模型'}"
            )
        return status

    @property
    def dim(self) -> int:
        """当前向量维度（降级模式下为 fallback_dim）。"""
        self._ensure_loaded()
        return self.profile.dimension

    # ---- 降级哈希嵌入 -----------------------------------------------------
    def _hash_embed(self, text: str) -> list[float]:
        """确定性 char/词 散列嵌入：token 经 md5 落桶累加，再 L2 归一化。

        非语义但稳定、含一定词面信号——足以让向量检索在降级时返回合理排序，
        并保证同一文本每次得到相同向量（可复现）。
        """
        dim = self.fallback_dim
        vec = [0.0] * dim
        tokens = _TOKEN_RE.findall((text or "").lower())
        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    """返回进程内 Embedder 单例。"""
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                _embedder = Embedder()
    return _embedder


def set_embedder_for_evaluation(embedder: Embedder | None) -> None:
    """离线评测进程选择明确Embedding模式；生产业务不得调用。"""
    global _embedder
    with _embedder_lock:
        _embedder = embedder
