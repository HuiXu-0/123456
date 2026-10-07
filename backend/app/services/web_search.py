"""可插拔联网搜索 Provider（接口文档 8.6 增量，C-fix 批3-bonus）。

把"联网搜索"抽象成可插拔接口，便于在不改上层聚合逻辑的前提下切换搜索源：
- `none`（默认）：无搜索能力 → `online=False`、`search()` 返回 []，上层走**种子兜底**；
- `tavily`：Tavily Web Search API（单密钥、LLM 友好），配置 `SEARCH_PROVIDER=tavily` +
  `SEARCH_API_KEY=...` 即启用真实联网；调用失败自动降级（不抛错，回 []）。

> **需要人工提供的搜索 API（任选其一即可启用真实联网）**：
> - Tavily（推荐，已内置）：`SEARCH_PROVIDER=tavily` + `SEARCH_API_KEY`（https://tavily.com）；
> - 其它（SerpAPI / Bing Web Search / YouTube Data API / arXiv）：实现一个 `SearchProvider`
>   子类即可接入，聚合层与接口签名不变。

返回的 `SearchHit` 仅含**真实命中**字段（title/url/source/snippet/type），URL 一律来自搜索
结果（不编造），交由聚合 Agent（LLMClient.aggregate_resources）整理 + critic 评分。
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.core.config import settings

logger = logging.getLogger("app.services.web_search")

# 资源类型映射（接口文档 8.6 type 枚举）
RESOURCE_TYPES = ("视频", "论文", "文档", "课程")


def _infer_type(url: str, title: str) -> str:
    """据 URL / 标题粗分资源类型（兜底，最终类型以聚合 Agent 评分为准）。"""
    u = (url or "").lower()
    t = (title or "").lower()
    if any(k in u for k in ("youtube.com", "youtu.be", "bilibili.com", "/video")):
        return "视频"
    if any(k in u for k in ("arxiv.org", "/pdf", "paperswithcode")):
        return "论文"
    if any(k in u for k in ("coursera", "udemy", "mooc", "/course", "edx.org")):
        return "课程"
    if "课程" in t or "公开课" in t:
        return "课程"
    if "论文" in t:
        return "论文"
    return "文档"


class SearchProvider(Protocol):
    """联网搜索 Provider 协议。"""

    name: str
    online: bool

    def search(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        """返回搜索命中 [{title,url,source,snippet,type}]；无能力/失败 → []。"""
        ...

    def search_images(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        """返回图片命中 [{url, source, description}]；无能力/失败 → []。

        URL 一律取自搜索结果（不编造），交由上层做防幻觉校验后嵌入讲义；用于讲义配图。
        """
        ...


class _OfflineProvider:
    """无搜索能力：online=False，search 恒返回 []（上层走种子兜底）。"""

    name = "none"
    online = False

    def search(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        return []

    def search_images(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        return []


class _TavilyProvider:
    """Tavily Web Search API（OpenAI/LLM 友好的联网搜索，单密钥）。"""

    name = "tavily"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.online = bool(api_key)

    def search(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        if not self.online:
            return []
        try:
            import requests  # 延迟导入，避免无搜索能力时的硬依赖

            resp = requests.post(
                f"{self.base_url}/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 联网失败不致命 → 降级种子兜底
            logger.warning("Tavily 搜索失败，降级种子兜底：%s", exc)
            return []
        hits: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:max_results]:
            url = str(r.get("url") or "").strip()
            title = str(r.get("title") or "").strip()
            if not url or not title:
                continue
            hits.append(
                {
                    "title": title,
                    "url": url,
                    "source": _source_of(url),
                    "snippet": str(r.get("content") or "")[:300],
                    "type": _infer_type(url, title),
                }
            )
        return hits

    def search_images(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        """Tavily 图片搜索（include_images）。URL 取自结果，失败/无能力 → []。"""
        if not self.online:
            return []
        try:
            import requests  # 延迟导入，避免无搜索能力时的硬依赖

            resp = requests.post(
                f"{self.base_url}/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_images": True,
                    "include_image_descriptions": True,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 联网失败不致命 → 不插图
            logger.warning("Tavily 图片搜索失败，跳过配图：%s", exc)
            return []
        hits: list[dict[str, Any]] = []
        for img in (data.get("images") or [])[:max_results]:
            # Tavily images 可能是 url 字符串或 {url, description}（开了描述时）
            if isinstance(img, str):
                url, desc = img.strip(), ""
            elif isinstance(img, dict):
                url, desc = str(img.get("url") or "").strip(), str(img.get("description") or "")
            else:
                continue
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            hits.append({"url": url, "source": _source_of(url), "description": desc})
        return hits


def _source_of(url: str) -> str:
    """从 URL 提取来源域名（展示用）。"""
    u = (url or "").split("//", 1)[-1]
    return u.split("/", 1)[0] or "web"


def get_provider() -> SearchProvider:
    """按 settings.search_provider 选择 Provider（无能力/无密钥 → offline 兜底）。"""
    provider = (settings.search_provider or "none").lower()
    if provider == "tavily" and settings.search_api_key:
        return _TavilyProvider(
            settings.search_api_key, settings.search_base_url, settings.search_timeout_seconds
        )
    return _OfflineProvider()
